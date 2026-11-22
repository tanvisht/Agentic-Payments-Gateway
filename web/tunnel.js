// Hero: a metallic AgentPay card inside a slowly rotating cylinder of transaction tiles.
// All textures are generated on canvas at runtime (no external imagery).
import * as THREE from "three";

const canvas = document.getElementById("tunnel");
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75));
renderer.setClearColor(0x0b0b0b, 1);
const scene = new THREE.Scene();
scene.fog = new THREE.Fog(0x0b0b0b, 9, 26);
const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
camera.position.set(0, 0.2, 6.6);

const TX = [
  ["$349.00", "sonicaudio.store", "SUCCEEDED"], ["$129.00", "stride.run", "CAPTURED"], ["$1,199.00", "voltgear.shop", "REQUIRES APPROVAL"],
  ["$39.00", "voltgear.shop", "SUCCEEDED"], ["$289.00", "quietstays.com", "AUTHORIZED"], ["0.35 USDC", "x402 · base", "SETTLED"],
  ["$240.00", "tablefor.two", "SUCCEEDED"], ["$19.00", "voltgear.shop", "REFUNDED"], ["$145.00", "trailhead.co", "ESCROW LOCKED"],
  ["$499.00", "voltgear.shop", "RISK REVIEW"], ["$35.00", "stride.run", "SUCCEEDED"], ["$329.00", "skyline.air", "SUCCEEDED"],
];
const SHADES = [["#2a2d30", "#121314"], ["#33302b", "#141210"], ["#22282b", "#0f1213"], ["#2d2a30", "#121014"]];

function tileTexture(i) {
  const c = document.createElement("canvas"); c.width = 360; c.height = 540;
  const g = c.getContext("2d");
  const [a, b] = SHADES[i % SHADES.length];
  const grd = g.createLinearGradient(0, 0, 360, 540); grd.addColorStop(0, a); grd.addColorStop(1, b);
  g.fillStyle = grd; g.fillRect(0, 0, 360, 540);
  // soft architectural light streak
  const s = g.createLinearGradient(0, 0, 360, 0);
  s.addColorStop(0, "rgba(255,255,255,0)"); s.addColorStop(0.5 + 0.3 * Math.sin(i), "rgba(255,255,255,0.09)"); s.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = s; g.fillRect(0, 60 + (i * 37) % 200, 360, 160);
  const [amt, who, st] = TX[i % TX.length];
  g.fillStyle = "rgba(255,255,255,0.86)"; g.font = "500 44px Inter Tight, sans-serif"; g.fillText(amt, 28, 420);
  g.fillStyle = "rgba(255,255,255,0.5)"; g.font = "400 22px JetBrains Mono, monospace"; g.fillText(who, 28, 462);
  g.fillStyle = st.includes("RISK") || st.includes("APPROVAL") ? "#f0c98d" : "#9fd3b4";
  g.font = "500 18px JetBrains Mono, monospace"; g.fillText("● " + st, 28, 500);
  g.strokeStyle = "rgba(255,255,255,0.08)"; g.strokeRect(1, 1, 358, 538);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4;
  return t;
}

// tunnel
const ROWS = 5, COLS = 16, R = 11, TW = 1.9, TH = TW * 1.5, GAP = 3.5;
const tunnel = new THREE.Group(); scene.add(tunnel);
const geo = new THREE.PlaneGeometry(TW, TH);
const rows = [];
for (let r = 0; r < ROWS; r++) {
  const row = new THREE.Group(); row.position.y = (r - (ROWS - 1) / 2) * GAP; tunnel.add(row); rows.push(row);
  for (let c = 0; c < COLS; c++) {
    const ang = ((c + (r % 2) * 0.5) / COLS) * Math.PI * 2;
    const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: tileTexture(r * COLS + c), side: THREE.DoubleSide, transparent: true, opacity: 0.92 }));
    m.position.set(Math.cos(ang) * R, 0, Math.sin(ang) * R);
    m.lookAt(0, 0, 0);
    row.add(m);
  }
}

// the card
function cardTexture() {
  const c = document.createElement("canvas"); c.width = 1024; c.height = 646;
  const g = c.getContext("2d");
  const base = g.createLinearGradient(0, 0, 1024, 646);
  base.addColorStop(0, "#d9dcdc"); base.addColorStop(0.42, "#9ea3a6"); base.addColorStop(0.43, "#1b1d1f"); base.addColorStop(1, "#050506");
  g.fillStyle = base; g.fillRect(0, 0, 1024, 646);
  for (let y = 0; y < 646; y += 2) { g.fillStyle = `rgba(255,255,255,${Math.random() * 0.035})`; g.fillRect(0, y, 1024, 1); }
  // chip
  g.fillStyle = "#b9a678"; g.beginPath(); g.roundRect(96, 230, 120, 92, 14); g.fill();
  g.strokeStyle = "rgba(0,0,0,.35)"; g.lineWidth = 2; for (const x of [136, 176]) { g.beginPath(); g.moveTo(x, 230); g.lineTo(x, 322); g.stroke(); }
  // mark
  g.strokeStyle = "rgba(255,255,255,0.92)"; g.lineWidth = 5; g.beginPath(); g.arc(512, 323, 92, 0, Math.PI * 2); g.stroke();
  g.lineWidth = 7; g.lineCap = "round"; g.lineJoin = "round";
  g.beginPath(); g.moveTo(462, 375); g.lineTo(512, 268); g.lineTo(562, 375); g.moveTo(482, 336); g.lineTo(542, 336); g.stroke();
  g.fillStyle = "rgba(255,255,255,0.9)"; g.font = "500 54px Inter Tight, sans-serif"; g.fillText("AGENTPAY", 96, 560);
  g.font = "500 26px JetBrains Mono, monospace"; g.fillStyle = "rgba(255,255,255,0.55)";
  g.fillText("MANDATE-BOUND · AGENT", 600, 560);
  g.fillStyle = "rgba(20,20,20,0.75)"; g.font = "500 30px JetBrains Mono, monospace"; g.fillText("•••• 4242", 96, 120);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
  return t;
}
const cardGeo = new THREE.BoxGeometry(3.4, 2.15, 0.035);
const edge = new THREE.MeshBasicMaterial({ color: 0x2a2c2e });
const face = new THREE.MeshBasicMaterial({ map: cardTexture() });
const card = new THREE.Mesh(cardGeo, [edge, edge, edge, edge, face, face]);
card.position.y = 0.85;
scene.add(card);

// light sweep across the card
function shineTexture() {
  const c = document.createElement("canvas"); c.width = 512; c.height = 64;
  const g = c.getContext("2d"); const grd = g.createLinearGradient(0, 0, 512, 0);
  grd.addColorStop(0, "rgba(255,255,255,0)"); grd.addColorStop(0.47, "rgba(255,255,255,0)");
  grd.addColorStop(0.5, "rgba(255,255,255,0.22)"); grd.addColorStop(0.53, "rgba(255,255,255,0)"); grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd; g.fillRect(0, 0, 512, 64);
  const t = new THREE.CanvasTexture(c); t.wrapS = THREE.RepeatWrapping; return t;
}
const shineTex = shineTexture();
// same footprint as the card face, so the highlight can never spill outside it
const sweep = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 2.15),
  new THREE.MeshBasicMaterial({ map: shineTex, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
sweep.position.z = 0.02; card.add(sweep);

function resize() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  renderer.setSize(w, h, false); camera.aspect = w / h;
  camera.position.z = w < 700 ? 9.2 : 6.6; camera.updateProjectionMatrix();
}
addEventListener("resize", resize); resize();

let mx = 0, my = 0;
addEventListener("pointermove", (e) => { mx = e.clientX / innerWidth - 0.5; my = e.clientY / innerHeight - 0.5; });
let scrollP = 0;
addEventListener("scroll", () => { scrollP = Math.min(1, scrollY / innerHeight); }, { passive: true });

const clock = new THREE.Clock();
let visible = true;
new IntersectionObserver(([e]) => (visible = e.isIntersecting)).observe(canvas);
function frame() {
  requestAnimationFrame(frame);
  if (!visible) return;
  const t = clock.getElapsedTime();
  const speed = reduce ? 0 : 1;
  rows.forEach((row, i) => (row.rotation.y = (i % 2 ? -1 : 1) * t * 0.045 * (0.7 + i * 0.15) * speed + scrollP * (i % 2 ? -0.8 : 0.8)));
  tunnel.position.y = scrollP * 2.4;
  card.rotation.y = Math.sin(t * 0.5) * 0.28 * speed + mx * 0.35 + scrollP * Math.PI * 0.6;
  card.rotation.x = Math.sin(t * 0.37) * 0.06 * speed - my * 0.18;
  card.position.y = 0.85 + Math.sin(t * 0.8) * 0.05 * speed - scrollP * 1.2;
  shineTex.offset.x = -((t * 0.18) % 1.6) + 0.3;
  renderer.render(scene, camera);
}
frame();
