// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

/// @title AgentEscrow
/// @notice Escrow rail for agent payments. Maps 1:1 onto the gateway's
///         authorize / capture / cancel lifecycle:
///           lock    = authorize (funds leave the payer, held by contract)
///           release = capture   (full or partial; remainder returns to payer)
///           refund  = cancel    (before release)
///         After the deadline anyone can trigger the refund, so funds can never
///         be stranded if the gateway (operator) disappears.
/// @dev    Checks-effects-interactions everywhere; one escrow per id; no admin
///         keys, no upgradeability, no owner withdrawal path.
contract AgentEscrow {
    enum Status { None, Locked, Released, Refunded }

    struct Escrow {
        address payer;
        address payee;
        address token;
        address operator;   // gateway / arbiter allowed to release or refund
        uint128 amount;
        uint64 deadline;
        Status status;
    }

    mapping(bytes32 => Escrow) public escrows;
    uint256 private _locked = 1;

    event Locked(bytes32 indexed id, address indexed payer, address indexed payee, address token, uint256 amount, uint64 deadline, address operator);
    event Released(bytes32 indexed id, uint256 toPayee, uint256 toPayer);
    event Refunded(bytes32 indexed id, uint256 amount, bool afterDeadline);

    error AlreadyExists();
    error NotLocked();
    error NotAuthorized();
    error BadAmount();
    error BadDeadline();
    error TransferFailed();
    error Reentrancy();

    modifier nonReentrant() {
        if (_locked != 1) revert Reentrancy();
        _locked = 2;
        _;
        _locked = 1;
    }

    function lock(bytes32 id, address payee, address token, uint128 amount, uint64 deadline, address operator)
        external
        nonReentrant
    {
        if (escrows[id].status != Status.None) revert AlreadyExists();
        if (amount == 0 || payee == address(0) || token == address(0)) revert BadAmount();
        if (deadline <= block.timestamp) revert BadDeadline();
        escrows[id] = Escrow(msg.sender, payee, token, operator, amount, deadline, Status.Locked);
        _pull(token, msg.sender, amount);
        emit Locked(id, msg.sender, payee, token, amount, deadline, operator);
    }

    /// @param amount amount to release to the payee (<= locked); remainder refunds the payer.
    function release(bytes32 id, uint128 amount) external nonReentrant {
        Escrow storage e = escrows[id];
        if (e.status != Status.Locked) revert NotLocked();
        if (msg.sender != e.payer && msg.sender != e.operator) revert NotAuthorized();
        if (amount == 0 || amount > e.amount) revert BadAmount();
        e.status = Status.Released;
        uint256 back = e.amount - amount;
        _push(e.token, e.payee, amount);
        if (back > 0) _push(e.token, e.payer, back);
        emit Released(id, amount, back);
    }

    function refund(bytes32 id) external nonReentrant {
        Escrow storage e = escrows[id];
        if (e.status != Status.Locked) revert NotLocked();
        bool expired = block.timestamp >= e.deadline;
        if (!expired && msg.sender != e.payer && msg.sender != e.operator && msg.sender != e.payee) revert NotAuthorized();
        e.status = Status.Refunded;
        _push(e.token, e.payer, e.amount);
        emit Refunded(id, e.amount, expired);
    }

    function _pull(address token, address from, uint256 amount) private {
        (bool ok, bytes memory data) = token.call(abi.encodeCall(IERC20.transferFrom, (from, address(this), amount)));
        if (!ok || (data.length != 0 && !abi.decode(data, (bool)))) revert TransferFailed();
    }

    function _push(address token, address to, uint256 amount) private {
        (bool ok, bytes memory data) = token.call(abi.encodeCall(IERC20.transfer, (to, amount)));
        if (!ok || (data.length != 0 && !abi.decode(data, (bool)))) revert TransferFailed();
    }
}
