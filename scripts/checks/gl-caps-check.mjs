// Turnkey post-build check that an anchor's WebGL capability limits reach the
// page. Exits non-zero on failure.
//
// This exists because the shipped 152.0.7977.83 build served NONE of them and
// looked healthy doing it. Every serving hook fell through to the host, so the
// numbers a page read were internally consistent, stable across runs and
// plausible -- they were simply the wrong device's. A detector needed one
// getParameter call to find an ANGLE Direct3D11 renderer string beside a
// 16384x16384 viewport where shader model 5 mandates 32767.
//
// What it asserts, and why it is shaped this way:
//
//   1 SERVED     every limit the anchor measured is what the page reads, for
//                each anchor in turn. Each launch is compared against ITS OWN
//                anchor, never against another launch. Comparing launches to
//                each other is what would have missed this: the broken build
//                returned byte-identical limits from every launch while
//                claiming three different GPUs, so an agreement test passes
//                precisely when the bug is present.
//
//   2 DISTINCT   the limits are not identical across anchors. Four different
//                measured devices that agree on every number are not four
//                devices, they are the host's table served four times. This
//                is the symptom, kept as its own assertion so a regression
//                names itself.
//
//   3 IDENTITY   the renderer string is one the catalogue pairs with THIS
//                anchor, so a fix to the limits cannot be bought by breaking
//                what already worked. Not "is a measured member": the product
//                rotates the identity inside an anchor on purpose -- a D3D11
//                NVIDIA persona is any common NVIDIA board, because ANGLE's
//                Renderer11 builds the string from the DXGI adapter
//                description and d3d11_gl::GenerateCaps derives every limit
//                from the feature level, never from the device id. So the
//                servable set is the option set that
//                resources/profiles/dispersion/gpu_identity.json keys on this
//                anchor. A string paired with a DIFFERENT anchor is still a
//                failure (that is the cross-anchor swap the retired family
//                catalogue shipped), and a string paired with no anchor at
//                all is the host showing through.
//
// Usage: node gl-caps-check.mjs [binaryPath] [repoRoot]
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';

const BIN = process.argv[2] ||
  '/tmp/apostate-bin/apostate-152.0.7977.83-linux-x64/chrome';
const ROOT = process.argv[3] || path.resolve(import.meta.dirname, '../..');
const PORT = 18089;

// The scalar keys an anchor stores, and how a page reads them. A two-element
// GL parameter is stored as two scalars, so it is checked through both.
const RANGES = {
  MAX_VIEWPORT_DIMS: ['MAX_VIEWPORT_DIMS_WIDTH', 'MAX_VIEWPORT_DIMS_HEIGHT'],
  ALIASED_LINE_WIDTH_RANGE: ['ALIASED_LINE_WIDTH_RANGE_MIN', 'ALIASED_LINE_WIDTH_RANGE_MAX'],
  ALIASED_POINT_SIZE_RANGE: ['ALIASED_POINT_SIZE_RANGE_MIN', 'ALIASED_POINT_SIZE_RANGE_MAX'],
};
// Every parameter the detector sweep found wrong. Checked by name so the list
// is auditable against the sweep rather than derived from the code under test.
const FLAGGED = [
  'ALIASED_LINE_WIDTH_RANGE', 'ALIASED_POINT_SIZE_RANGE', 'MAX_VIEWPORT_DIMS',
  'MAX_VERTEX_UNIFORM_VECTORS', 'MAX_ELEMENTS_INDICES', 'MAX_ELEMENTS_VERTICES',
  'MAX_PROGRAM_TEXEL_OFFSET', 'MAX_SAMPLES', 'MAX_SERVER_WAIT_TIMEOUT',
  'MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS', 'MAX_VERTEX_UNIFORM_COMPONENTS',
  'MIN_PROGRAM_TEXEL_OFFSET', 'UNIFORM_BUFFER_OFFSET_ALIGNMENT',
];

// Which renderer strings the catalogue pairs with which anchor. Read from the
// table the binary was generated from, so widening the identity pool cannot
// make this check fail and cannot make it stop checking either: the pairing is
// what it asserts, not a hardcoded list.
function catalogueIdentities() {
  const table = JSON.parse(fs.readFileSync(
    path.join(ROOT, 'resources/profiles/dispersion/gpu_identity.json'), 'utf8'));
  const byAnchor = new Map();
  for (const set of table.option_sets || []) {
    const anchor = set.key && set.key.anchor;
    if (!anchor) continue;
    const renderers = new Set();
    for (const option of set.options || []) {
      const renderer = option.value && option.value.gpu
        && option.value.gpu.unmasked_renderer;
      if (renderer) renderers.add(renderer);
    }
    byAnchor.set(anchor, renderers);
  }
  return byAnchor;
}
const IDENTITIES = catalogueIdentities();

// What each anchor measured, read from the corpus rather than hardcoded, so
// this check cannot drift away from the data the binary was built from.
function anchorExpectations(file) {
  const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!raw.capability_cluster) return null;
  const params = {};
  for (const ctx of ['webgl1', 'webgl2']) {
    const section = raw.capability_cluster[ctx];
    if (!section || !section.parameters) continue;
    for (const [k, v] of Object.entries(section.parameters)) {
      if (!(k in params)) params[k] = v;
    }
  }
  const members = raw.members || [];
  const measured = members
    .map((m) => m.identity && m.identity.webgl1 && m.identity.webgl1.unmaskedRenderer)
    .filter(Boolean);
  // Every measured member is also a catalogue option, but union them anyway:
  // an anchor with no option set would otherwise silently assert nothing.
  const servable = new Set([...(IDENTITIES.get(raw.anchor_id) || []), ...measured]);
  return { id: raw.anchor_id, params, measured, servable };
}

const srv = http.createServer((q, s) => {
  if (q.method === 'POST') {
    let b = ''; q.on('data', (c) => { b += c; });
    q.on('end', () => { s.end('ok'); srv.emit('p', JSON.parse(b)); });
    return;
  }
  s.writeHead(200, { 'content-type': 'text/html' });
  s.end(`<!doctype html><meta charset=utf-8><script>
  const names=${JSON.stringify(FLAGGED)};
  const c=document.createElement('canvas');
  let g=null; try{g=c.getContext('webgl2');}catch(e){}
  const out={};
  if(g){for(const n of names){let v=g.getParameter(g[n]);
    if(v&&v.length!==undefined&&typeof v!=='string')v=Array.from(v);out[n]=v;}}
  let d=null; try{d=g&&g.getExtension('WEBGL_debug_renderer_info');}catch(e){}
  fetch('/c',{method:'POST',body:JSON.stringify({params:out,
    renderer:d?g.getParameter(d.UNMASKED_RENDERER_WEBGL):null,
    context:!!g})});
  </script>`);
});
await new Promise((r) => srv.listen(PORT, '127.0.0.1', r));

async function launch(anchorId) {
  const u = fs.mkdtempSync(path.join(os.tmpdir(), 'glcaps-'));
  const args = [`--user-data-dir=${u}`, '--no-first-run', '--no-default-browser-check',
    `--fingerprint-anchor=${anchorId}`, '--no-sandbox', '--disable-gpu-sandbox',
    `http://127.0.0.1:${PORT}/`];
  const child = spawn(BIN, args, { stdio: 'ignore' });
  const got = await new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), 90000);
    srv.once('p', (v) => { clearTimeout(timer); resolve(v); });
  });
  try { process.kill(child.pid, 'SIGKILL'); } catch { /* already gone */ }
  fs.rmSync(u, { recursive: true, force: true });
  return got;
}

const files = fs.readdirSync(path.join(ROOT, 'corpus/anchors'))
  .filter((f) => f.endsWith('.json')).sort();
let failures = 0;
const seen = [];

for (const f of files) {
  const want = anchorExpectations(path.join(ROOT, 'corpus/anchors', f));
  if (!want) continue;
  const got = await launch(want.id);
  if (!got || !got.context) {
    console.log(`FAIL ${want.id}: no WebGL2 context`);
    failures++; continue;
  }
  const rows = [];
  let bad = 0;
  for (const name of FLAGGED) {
    if (!(name in want.params)) continue;
    const expected = want.params[name];
    const actual = got.params[name];
    const ok = JSON.stringify(expected) === JSON.stringify(actual);
    if (!ok) { bad++; rows.push(`       ${name}: anchor ${JSON.stringify(expected)} served ${JSON.stringify(actual)}`); }
  }
  if (want.servable.size && !want.servable.has(got.renderer)) {
    bad++;
    const elsewhere = [...IDENTITIES].find(([, set]) => set.has(got.renderer));
    rows.push(`       renderer: served ${JSON.stringify(got.renderer)}, `
      + (elsewhere
        ? `which the catalogue pairs with ${elsewhere[0]}, not this anchor`
        : 'which the catalogue pairs with no anchor at all')
      + ` (${want.servable.size} servable here, `
      + `${want.measured.length} of them measured)`);
  }
  console.log(`${bad ? 'FAIL' : ' ok '} ${want.id}`);
  for (const r of rows) console.log(r);
  failures += bad ? 1 : 0;
  seen.push(JSON.stringify(got.params));
}

// 2 DISTINCT. Only meaningful with more than one anchor checked.
if (seen.length > 1 && new Set(seen).size === 1) {
  console.log('FAIL DISTINCT: every anchor served byte-identical limits, which is '
    + 'the host\'s table served N times rather than N devices');
  failures++;
}

srv.close();
console.log(failures ? `\n${failures} failure(s)` : `\n${seen.length} anchor(s) serve their own measured limits`);
process.exit(failures ? 1 : 0);
