// Offline cinematic render of the hero film. Deterministic: renderAt(t) draws the frame at time t,
// so the recorder can capture a seamless loop frame by frame. Original art, generated on canvas.
import * as THREE from "three";

const W = 1920, H = 1080, LOOP = 12;              // seconds
const canvas = document.getElementById("c");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(1); renderer.setSize(W, H, false);
renderer.setClearColor(0x070707, 1);
renderer.outputColorSpace = THREE.SRGBColorSpace;
const scene = new THREE.Scene();
scene.fog = new THREE.Fog(0x070707, 10, 26);
const camera = new THREE.PerspectiveCamera(42, W / H, 0.1, 100);

// deterministic PRNG so every render is identical
let seed = 7; const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);

// ---- "architectural photograph" tiles: dim rooms, light wells, long tables, corridors
function roomTexture(i) {
  const c = document.createElement("canvas"); c.width = 512; c.height = 720;
  const g = c.getContext("2d");
  const warm = i % 3 === 0;
  const wall = g.createLinearGradient(0, 0, 0, 720);
  wall.addColorStop(0, warm ? "#2b2622" : "#22272a"); wall.addColorStop(0.62, warm ? "#16120f" : "#111416"); wall.addColorStop(1, "#060606");
  g.fillStyle = wall; g.fillRect(0, 0, 512, 720);
  const kind = i % 5;
  if (kind === 0) { // light well from ceiling
    const l = g.createRadialGradient(256, 80, 10, 256, 80, 260); l.addColorStop(0, "rgba(255,240,220,.55)"); l.addColorStop(1, "rgba(255,240,220,0)");
    g.fillStyle = l; g.fillRect(0, 0, 512, 400);
    g.fillStyle = "rgba(255,245,230,.85)"; g.beginPath(); g.ellipse(256, 70, 70, 14, 0, 0, Math.PI * 2); g.fill();
  } else if (kind === 1) { // long table in perspective
    g.fillStyle = "rgba(200,205,210,.18)"; g.beginPath(); g.moveTo(150, 720); g.lineTo(362, 720); g.lineTo(276, 420); g.lineTo(236, 420); g.closePath(); g.fill();
    for (let k = 0; k < 7; k++) { const y = 440 + k * 40, s = 0.25 + k * 0.12; g.fillStyle = "rgba(10,10,10,.9)"; g.fillRect(256 - 70 * s - 18 * s, y, 16 * s, 34 * s); g.fillRect(256 + 70 * s, y, 16 * s, 34 * s); }
    g.fillStyle = "rgba(220,230,240,.25)"; g.fillRect(200, 140, 112, 180);
  } else if (kind === 2) { // corridor with lit end wall
    g.fillStyle = "rgba(235,225,205,.5)"; g.fillRect(222, 250, 68, 230);
    g.strokeStyle = "rgba(255,255,255,.06)"; g.lineWidth = 2;
    for (const [x1, x2] of [[0, 222], [512, 290]]) { g.beginPath(); g.moveTo(x1, 0); g.lineTo(x2, 250); g.moveTo(x1, 720); g.lineTo(x2, 480); g.stroke(); }
  } else if (kind === 3) { // window with city glow
    const w = g.createLinearGradient(80, 120, 430, 560); w.addColorStop(0, "rgba(150,170,190,.35)"); w.addColorStop(1, "rgba(40,50,60,.2)");
    g.fillStyle = w; g.fillRect(80, 120, 350, 440);
    for (let k = 0; k < 40; k++) { g.fillStyle = `rgba(255,220,170,${0.2 + rnd() * 0.5})`; g.fillRect(90 + rnd() * 330, 380 + rnd() * 160, 2, 2); }
    g.fillStyle = "rgba(0,0,0,.85)"; g.fillRect(250, 120, 6, 440);
  } else { // sculptural sphere on plinth
    const s = g.createRadialGradient(230, 300, 10, 256, 330, 130); s.addColorStop(0, "rgba(220,220,220,.6)"); s.addColorStop(1, "rgba(30,30,30,.2)");
    g.fillStyle = s; g.beginPath(); g.arc(256, 330, 120, 0, Math.PI * 2); g.fill();
    g.fillStyle = "rgba(180,180,180,.12)"; g.fillRect(186, 450, 140, 270);
  }
  // floor sheen + vignette + film grain
  const fl = g.createLinearGradient(0, 560, 0, 720); fl.addColorStop(0, "rgba(255,255,255,0)"); fl.addColorStop(1, "rgba(255,255,255,.05)");
  g.fillStyle = fl; g.fillRect(0, 560, 512, 160);
  const v = g.createRadialGradient(256, 360, 120, 256, 360, 520); v.addColorStop(0, "rgba(0,0,0,0)"); v.addColorStop(1, "rgba(0,0,0,.7)");
  g.fillStyle = v; g.fillRect(0, 0, 512, 720);
  const img = g.getImageData(0, 0, 512, 720);
  for (let p = 0; p < img.data.length; p += 4) { const n = (rnd() - 0.5) * 14; img.data[p] += n; img.data[p + 1] += n; img.data[p + 2] += n; }
  g.putImageData(img, 0, 0);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8; return t;
}

const ROWS = 5, COLS = 18, R = 12, TW = 2.0, TH = 2.8, GAP = 3.6;
const rows = [];
for (let r = 0; r < ROWS; r++) {
  const row = new THREE.Group(); row.position.y = (r - (ROWS - 1) / 2) * GAP; scene.add(row); rows.push(row);
  for (let c = 0; c < COLS; c++) {
    const a = ((c + (r % 2) * 0.5) / COLS) * Math.PI * 2;
    const m = new THREE.Mesh(new THREE.PlaneGeometry(TW, TH), new THREE.MeshBasicMaterial({ map: roomTexture(r * 7 + c), side: THREE.DoubleSide, color: new THREE.Color(1.35, 1.35, 1.35) }));
    m.position.set(Math.cos(a) * R, 0, Math.sin(a) * R); m.lookAt(0, 0, 0); row.add(m);
  }
}

// ---- portrait metal card
function cardTexture() {
  const c = document.createElement("canvas"); c.width = 640; c.height = 1010;
  const g = c.getContext("2d");
  const b = g.createLinearGradient(0, 0, 640, 1010);
  b.addColorStop(0, "#e9ecec"); b.addColorStop(0.4, "#a7acaf"); b.addColorStop(0.41, "#151718"); b.addColorStop(1, "#030303");
  g.fillStyle = b; g.fillRect(0, 0, 640, 1010);
  for (let y = 0; y < 1010; y += 2) { g.fillStyle = `rgba(255,255,255,${rnd() * 0.03})`; g.fillRect(0, y, 640, 1); }
  g.fillStyle = "rgba(20,20,20,.6)"; g.font = "500 30px 'JetBrains Mono', monospace"; g.fillText("MANDATE-BOUND", 70, 110);
  g.strokeStyle = "rgba(255,255,255,.92)"; g.lineWidth = 6; g.beginPath(); g.arc(320, 505, 120, 0, Math.PI * 2); g.stroke();
  g.lineWidth = 9; g.lineCap = "round"; g.lineJoin = "round";
  g.beginPath(); g.moveTo(255, 575); g.lineTo(320, 430); g.lineTo(385, 575); g.moveTo(282, 525); g.lineTo(358, 525); g.stroke();
  g.fillStyle = "#b9a678"; g.beginPath(); g.roundRect(70, 160, 110, 84, 12); g.fill();
  g.save(); g.translate(110, 930); g.rotate(-Math.PI / 2);
  g.fillStyle = "rgba(255,255,255,.9)"; g.font = "500 56px Inter Tight, Arial, sans-serif"; g.fillText("AGENTPAY", 0, 0); g.restore();
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8; return t;
}
const edge = new THREE.MeshBasicMaterial({ color: 0x2b2d2f });
const card = new THREE.Mesh(new THREE.BoxGeometry(2.0, 3.16, 0.04), [edge, edge, edge, edge, new THREE.MeshBasicMaterial({ map: cardTexture() }), new THREE.MeshBasicMaterial({ color: 0x0c0c0d })]);
card.position.y = 0.35; scene.add(card);

// soft halo behind the card (fake bloom)
const hc = document.createElement("canvas"); hc.width = hc.height = 256;
const hg = hc.getContext("2d"); const hr = hg.createRadialGradient(128, 128, 0, 128, 128, 128);
hr.addColorStop(0, "rgba(255,255,255,.09)"); hr.addColorStop(1, "rgba(255,255,255,0)"); hg.fillStyle = hr; hg.fillRect(0, 0, 256, 256);
const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(hc), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
halo.scale.set(9, 9, 1); halo.position.set(0, 0.35, -1.2); scene.add(halo);

// specular sweep confined to the card face
const sc = document.createElement("canvas"); sc.width = 512; sc.height = 16;
const sg = sc.getContext("2d"); const sl = sg.createLinearGradient(0, 0, 512, 0);
sl.addColorStop(0.44, "rgba(255,255,255,0)"); sl.addColorStop(0.5, "rgba(255,255,255,.35)"); sl.addColorStop(0.56, "rgba(255,255,255,0)");
sg.fillStyle = sl; sg.fillRect(0, 0, 512, 16);
const shineTex = new THREE.CanvasTexture(sc); shineTex.wrapS = THREE.RepeatWrapping;
const shine = new THREE.Mesh(new THREE.PlaneGeometry(2.0, 3.16), new THREE.MeshBasicMaterial({ map: shineTex, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
shine.position.z = 0.025; card.add(shine);

const TAU = Math.PI * 2;
window.renderAt = function (t) {
  const u = (t % LOOP) / LOOP;                       // 0..1, loops seamlessly
  rows.forEach((row, i) => (row.rotation.y = (i % 2 ? -1 : 1) * u * (TAU / COLS) * (2 + i % 3)));
  camera.position.set(Math.sin(u * TAU) * 0.6, 0.35 + Math.sin(u * TAU * 2) * 0.12, 7.4 + Math.cos(u * TAU) * 0.9);
  camera.lookAt(0, 0.3, 0);
  card.rotation.y = Math.sin(u * TAU) * 0.55;
  card.rotation.x = Math.sin(u * TAU * 2) * 0.05;
  card.rotation.z = Math.sin(u * TAU) * 0.03;
  shineTex.offset.x = -((u * 2) % 1) * 1.6 + 0.4;
  renderer.render(scene, camera);
};
window.LOOP = LOOP;
window.ready = true;
