const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const {test, before} = require('node:test');
const brs = require('brs');
const task = fs.readFileSync(path.join(__dirname, '../roku-receiver/components/MovieLightTask.brs'), 'utf8');
// The interpreter lacks Roku's tokenize method; split is equivalent for these IP fixtures.
const mocked = task.replace('address.tokenize(".")', 'address.split(".")')
  .replace('sleep(settings.transition_ms + 200)', 'mockFade(settings.transition_ms + 200)')
  .replace(/function movieLightBatch\(requests as Object\) as Object[\s\S]*?end function/, `
function movieLightBatch(requests as Object) as Object
    m.batches.push(requests.count())
    replies = []
    for each request in requests
        reply = invalid
        if not m.offline.doesExist(request.ip)
            product = m.bulbs[request.ip]
            command = request.request["smartlife.iot.smartbulb.lightingservice"]
            if command = invalid
                reply = {system: {get_sysinfo: product}}
            else
                desired = command.transition_light_state
                if not m.failures.doesExist(request.ip)
                    product.light_state.on_off = desired.on_off
                    if desired.brightness <> invalid then product.light_state.brightness = desired.brightness
                end if
                if not m.lostAcknowledgements.doesExist(request.ip) then reply = {"smartlife.iot.smartbulb.lightingservice": {transition_light_state: {err_code: 0}}}
            end if
        end if
        replies.push(reply)
    end for
    return replies
end function`);
const fixture = `
sub mockFade(milliseconds as Integer)
    m.fades.push(milliseconds)
end sub
sub main()
    first = {ip: "10.0.0.7", name: "Shelf", model: "KL110", device_id: "shelf"}
    second = {ip: "10.0.0.8", name: "Desk", model: "KL125", device_id: "desk"}
    m.bulbs = {
        "10.0.0.7": {model: "KL110", deviceId: "shelf", light_state: {on_off: 1, brightness: 65}},
        "10.0.0.8": {model: "KL125", deviceId: "desk", light_state: {on_off: 1, brightness: 80}}
    }
    m.batches = []
    m.fades = []
    m.offline = {}
    m.failures = {}
    m.lostAcknowledgements = {}
    m.top = {settings: {targets: [first, second], brightness: 10, paused_brightness: 90, transition_ms: 0}, action: "dim", snapshot: {}}
    changeMovieLight()
    dimmed = ParseJson(FormatJson(m.top.result))
    m.top.snapshot = dimmed.snapshot
    m.top.action = "pause"
    changeMovieLight()
    paused = ParseJson(FormatJson(m.top.result))
    m.top.snapshot = paused.snapshot
    m.top.action = "dim"
    changeMovieLight()
    resumed = ParseJson(FormatJson(m.top.result))
    m.top.snapshot = resumed.snapshot
    m.top.action = "restore"
    m.failures[second.ip] = true
    changeMovieLight()
    partial = ParseJson(FormatJson(m.top.result))
    m.top.snapshot = partial.remaining_snapshots
    m.failures = {}
    changeMovieLight()
    restored = ParseJson(FormatJson(m.top.result))
    m.top.action = "dim"
    m.top.snapshot = {}
    m.offline[first.ip] = true
    m.lostAcknowledgements[second.ip] = true
    changeMovieLight()
    offline = ParseJson(FormatJson(m.top.result))
    m.offline = {}
    m.lostAcknowledgements = {}
    m.bulbs[first.ip].deviceId = "replacement"
    m.top.snapshot = {}
    changeMovieLight()
    mismatch = ParseJson(FormatJson(m.top.result))
    m.bulbs[first.ip].deviceId = first.device_id
    m.bulbs[first.ip].light_state.on_off = 0
    m.top.snapshot = {}
    changeMovieLight()
    offDim = ParseJson(FormatJson(m.top.result))
    m.top.snapshot = offDim.snapshot
    m.top.action = "pause"
    changeMovieLight()
    offPause = ParseJson(FormatJson(m.top.result))
    m.top.snapshot = offPause.snapshot
    m.top.action = "restore"
    changeMovieLight()
    offRestore = ParseJson(FormatJson(m.top.result))
    print FormatJson({"dim": dimmed, paused: paused, resumed: resumed, partial: partial, restored: restored, offline: offline, mismatch: mismatch, offDim: offDim, offPause: offPause, offRestore: offRestore, batches: m.batches, fades: m.fades})
end sub
`;
let result;
before(async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'm3u-light-group-'));
  const filename = path.join(directory, 'test.brs'); fs.writeFileSync(filename, mocked + fixture);
  let stdout = '', stderr = '';
  try {
    try {
      await brs.execute([filename], {root: directory, componentDirs: [],
        stdout: {write(text) { stdout += text; }}, stderr: {write(text) { stderr += text; }}});
    } catch (error) { throw new Error(stderr || String(error)); }
    assert.equal(stderr, ''); result = JSON.parse(stdout.trim());
  } finally { fs.rmSync(directory, {recursive: true, force: true}); }
});
test('light-group worker preserves original states through pause/resume and partial restore', () => {
    assert.equal(result.dim.ok, true);
    assert.deepEqual(result.dim.snapshot, {shelf: {on_off: 1, brightness: 65}, desk: {on_off: 1, brightness: 80}});
    assert.deepEqual(result.paused.snapshot, result.dim.snapshot);
    assert.deepEqual(result.resumed.snapshot, result.dim.snapshot);
    assert.equal(result.partial.ok, false);
    assert.deepEqual(Object.keys(result.partial.remaining_snapshots), ['desk']);
    assert.equal(result.restored.ok, true); assert.deepEqual(result.restored.remaining_snapshots, {});
    assert.equal(result.restored.lights[0].after.brightness, 80);
    assert.deepEqual(result.batches.slice(0, 3), [2, 2, 2]);
});
test('one unavailable light does not block another, including a lost acknowledgement', () => {
  assert.equal(result.offline.ok, false);
  assert.equal(result.offline.lights[0].error, 'light_unavailable');
  assert.equal(result.offline.lights[1].ok, true);
  assert.equal(result.offline.lights[1].acknowledged, false);
  assert.deepEqual(result.offline.snapshot.desk, {on_off: 1, brightness: 80});
});
test('a different device at the saved IP is never controlled', () => {
  assert.equal(result.mismatch.lights[0].error, 'light_identity_mismatch');
  assert.equal(result.mismatch.lights[0].command_sent, false);
  assert.equal(result.mismatch.lights[1].ok, true);
});
test('an initially off bulb stays off on play and restores off after pausing', () => {
  assert.equal(result.offDim.lights[0].skipped, 'already_off');
  assert.equal(result.offDim.lights[0].command_sent, false);
  assert.equal(result.offPause.snapshot.shelf.on_off, 0);
  assert.equal(result.offRestore.lights[0].after.on_off, 0);
  assert.equal(result.offRestore.ok, true);
  assert.equal(result.fades.length, 10);
});
