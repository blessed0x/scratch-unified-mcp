/**
 * Headless Scratch runtime for **running and testing** projects, built on
 * {@link https://github.com/TurboWarp/scratch-vm TurboWarp's scratch-vm} (the
 * fork with a JIT compiler). It runs entirely in this Node process — no browser,
 * no WebGL — and exposes the project's *runtime* state as structured data an
 * agent can assert on: variables, lists, monitors, sprite positions, say/think
 * bubbles, the current question, running-thread count and any runtime errors.
 *
 * The VM is heavy, so it is `require`d lazily on first use: a server that only
 * edits projects never pays for it.
 *
 * What is intentionally absent and why it is fine:
 *   - **No renderer.** Costume *metadata* still loads (names, costume number),
 *     so logic that switches costumes by name/number works. Pixel output and the
 *     handful of renderer-backed blocks (touching-colour/sprite/edge, pen) are
 *     covered instead by the live TurboWarp editor via the screenshot bridge.
 *   - **No audio engine.** Sounds don't play; their blocks no-op.
 *
 * scratch-vm logs these absences once per asset through its bundled `nanolog`,
 * which ignores `disable()`; we mute the output streams around VM calls instead.
 *
 * @module scratch-mcp/runtime
 */
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

/** @type {typeof import('scratch-vm') | null} */
let VM = null;
/** @type {(new () => object) | null} */
let ScratchStorage = null;

/** Lazily load the VM and storage modules (both CommonJS). */
function ensureDeps() {
  if (VM) return;
  // TurboWarp's Runtime._step reads `document.hidden` whenever a renderer is
  // attached (`if (!document.hidden && !this.frameLoop._interpolationAnimation)`
  // before renderer.draw()). Headless node has no document, so a renderer now
  // crashes the first _step with "document is not defined". A minimal shim
  // keeps that branch alive and drawing no-ops.
  if (typeof globalThis.document === 'undefined') {
    globalThis.document = { hidden: false };
  }
  VM = require('scratch-vm');
  ScratchStorage = require('scratch-storage').ScratchStorage;
}

/**
 * Run `fn` with stdout/stderr writes swallowed. scratch-vm's logger writes
 * straight to the streams, so this is the only reliable way to keep its
 * "no renderer / no audio" chatter out of the MCP stdio channel. `fn` must be
 * synchronous or return a promise that settles quickly; we restore on both
 * success and failure.
 *
 * @template T
 * @param {() => T} fn
 * @returns {T}
 */
function muted(fn) {
  const out = process.stdout.write;
  const err = process.stderr.write;
  process.stdout.write = () => true;
  process.stderr.write = () => true;
  const restore = () => {
    process.stdout.write = out;
    process.stderr.write = err;
  };
  try {
    const result = fn();
    if (result && typeof result.then === 'function') {
      return result.finally(restore);
    }
    restore();
    return result;
  } catch (e) {
    restore();
    throw e;
  }
}

/** @param {Uint8Array} bytes @returns {ArrayBuffer} A standalone copy. */
const toArrayBuffer = (bytes) =>
  bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);

const delay = (ms) => new Promise((r) => setTimeout(r, ms));

/** Cap on the tool-facing event log between drains, to bound memory. */
const MAX_EVENT_LOG = 1000;

/**
 * Drives one project through a headless scratch-vm, kept loaded between calls
 * so an agent can green-flag, step, inspect, send input and repeat.
 */
export class HeadlessRuntime {
  constructor() {
    /** @type {import('scratch-vm') | null} */
    this.vm = null;
    /**
     * Last say/think bubble seen per target id, used only to de-duplicate the
     * event timeline (so a `say` in a loop logs one event). The snapshot's
     * `bubbles` are read live from each target instead (see {@link bubbleOf}).
     * @type {Map<string, object>}
     */
    this.bubbles = new Map();
    /** Text of the pending `ask and wait` question, or null. @type {string|null} */
    this.question = null;
    /** Runtime errors seen since the last green flag. @type {string[]} */
    this.errors = [];
    /**
     * Last mouse button state + stage position. Headless scratch-vm has no
     * renderer, so its Mouse device can never pick a sprite (every click
     * resolves to the Stage). We track the transitions here and fire
     * `event_whenthisspriteclicked` hats ourselves from {@link input}.
     * @type {{ down: boolean, x: number, y: number }}
     */
    this._mouse = { down: false, x: 0, y: 0 };
    /** Same shape, prior input — for detecting down/up transitions. @private */
    this._prevMouse = { down: false, x: 0, y: 0 };
    /**
     * Optional sink for notable runtime events, set by the server to forward
     * them as MCP log notifications. Receives `{ level, type, message,
     * ...fields }`. Independent of the tool-facing event log below: events are
     * always recorded for {@link drainEvents}, whether or not this is set.
     * @type {((event: object) => void) | null}
     */
    this.onEvent = null;
    /**
     * Tool-facing event log: every event since the last {@link drainEvents},
     * returned by `vm_run`. Capped at {@link MAX_EVENT_LOG} (oldest dropped).
     * @type {object[]}
     */
    this._eventLog = [];
    /** How many events were dropped from `_eventLog` to stay under the cap. */
    this._droppedEvents = 0;
    /**
     * Events awaiting delivery to {@link onEvent}. Drained by {@link _flush},
     * which is called only where stdout is live (never mid-muted-step).
     * @type {object[]}
     */
    this._notifyQueue = [];
    /**
     * Stub call recorder, reset per load by {@link _attachHeadlessStubs}:
     * `{ pen: [...], sound: [...] }`. Read via {@link stubCallsOf}.
     * @type {{ pen: object[], sound: object[] }}
     */
    this.stubCalls = { pen: [], sound: [] };
    /**
     * Offline sound-mix timeline, reset per load: `[{ atMs, pcm, rate }]`.
     * Read via {@link mixWav}. Initialized here so mixWav() before any load
     * returns silence instead of throwing on undefined.
     * @type {object[]}
     */
    this._mix = [];
    /**
     * Pen raster placeholder (real buffer installed per load by
     * {@link _attachHeadlessStubs}). Initialized here so penPng() before
     * any load throws the "no project" error, not a null dereference.
     * @type {{ width: number, height: number, data: Uint8ClampedArray } | null}
     */
    this.penCanvas = null;
    /**
     * Variable watches: name → last seen `{ target, name, value }`.
     * Set by {@link watch}, read on every call (poll-and-diff).
     * @type {Map<string, object>}
     */
    this._watches = new Map();
  }

  /**
   * Record a notable event. It goes to two independent places: the tool-facing
   * `_eventLog` (always, so `vm_run` can return it) and, if a sink is attached,
   * the `_notifyQueue` for MCP log notifications. Notifications are queued
   * rather than sent inline because many events fire synchronously inside a
   * muted VM step, where a stdout write would be swallowed by the mute.
   *
   * @param {string} level - An MCP log level (`debug`, `info`, `error`, …).
   * @param {string} type - Short event kind, e.g. `say`, `broadcast`.
   * @param {string} message - Human-readable one-liner.
   * @param {object} [fields] - Extra structured data.
   * @private
   */
  _emit(level, type, message, fields = {}) {
    const event = { level, type, message, ...fields };
    this._eventLog.push(event);
    if (this._eventLog.length > MAX_EVENT_LOG) {
      this._eventLog.shift();
      this._droppedEvents++;
    }
    if (this.onEvent) this._notifyQueue.push(event);
  }

  /** Deliver and clear queued log notifications. Call only when stdout is live. @private */
  _flush() {
    if (!this.onEvent || this._notifyQueue.length === 0) return;
    const batch = this._notifyQueue;
    this._notifyQueue = [];
    for (const event of batch) this.onEvent(event);
  }

  /**
   * Return and clear the events recorded since the last call. This is the
   * AI-facing event timeline (`vm_run` includes it in its result).
   *
   * @returns {{ events: object[], dropped: number }}
   */
  drainEvents() {
    const events = this._eventLog;
    const dropped = this._droppedEvents;
    this._eventLog = [];
    this._droppedEvents = 0;
    return { events, dropped };
  }

  /** @returns {import('scratch-vm')} The loaded VM, or throws. */
  _vm() {
    if (!this.vm)
      throw new Error(
        'No project loaded in the runtime. Call `vm_load` first.',
      );
    return this.vm;
  }

  /**
   * Load project bytes (an in-memory `.sb3`) into a fresh VM, replacing any
   * previously loaded project. Returns a summary of what loaded.
   *
   * @param {Uint8Array} bytes
   * @returns {Promise<object>}
   */
  async loadFromBytes(bytes) {
    ensureDeps();
    this.dispose();
    const vm = muted(() => {
      // Constructing the VM also touches a process-global "central dispatch",
      // which warns when replaced on a second load — mute it along with the load.
      const v = new VM();
      v.attachStorage(new ScratchStorage());
      v.clear();
      return v;
    });
    this.vm = vm;
    // Keep the JIT enabled. TurboWarp's JIT inserts a per-frame fence in
    // forever loops; the interpreter has no such fence and a non-yielding
    // forever body (no wait/glide) SPINS at CPU speed, teleporting sprites.
    // (This bit us: a bad `operator_pickrandom` opcode made a clone hat
    // fail JIT compilation, fall back to the interpreter, and sprites
    // crossed the stage in under a second. `operator_random` is the real
    // sb3 name.)
    this._wireEvents();
    await muted(() => vm.loadProject(toArrayBuffer(bytes)));
    this._patchHeadlessTouching(vm);
    this._attachHeadlessStubs(vm);
    const summary = this.summary();
    this._emit('info', 'load', `loaded ${summary.targets.length} targets`, {
      targets: summary.targets.map((t) => t.name),
    });
    this._flush();
    return summary;
  }

  /** Subscribe to the runtime events we surface as state and log events. @private */
  _wireEvents() {
    const vm = this.vm;
    const rt = vm.runtime;

    // SAY fires for both `say` and `think`; empty text clears the bubble. We
    // only emit a log event when the bubble actually changes, so a `say` inside
    // a loop doesn't spam one event per frame. It is emitted on the *runtime*,
    // not the VM (the VM doesn't forward it), so listen there.
    rt.on('SAY', (target, type, text) => {
      const id = target?.id;
      if (!id) return;
      const name = target.getName?.() ?? id;
      if (text === '' || text == null) {
        if (this.bubbles.delete(id))
          this._emit('debug', 'bubble', `${name} bubble cleared`, {
            sprite: name,
          });
        return;
      }
      const next = { sprite: name, type, text: String(text) };
      const prev = this.bubbles.get(id);
      this.bubbles.set(id, next);
      if (!prev || prev.text !== next.text || prev.type !== next.type)
        this._emit(
          'info',
          type,
          `${name} ${type}s: ${JSON.stringify(next.text)}`,
          {
            sprite: name,
            text: next.text,
          },
        );
    });

    // QUESTION carries the prompt string while an `ask and wait` is pending,
    // and null once it is answered. Also emitted on the runtime, not the VM.
    rt.on('QUESTION', (question) => {
      this.question = question == null ? null : String(question);
      if (this.question !== null)
        this._emit(
          'info',
          'question',
          `asks: ${JSON.stringify(this.question)}`,
          {
            text: this.question,
          },
        );
    });

    rt.on('RUNTIME_ERROR', (msg) => {
      this.errors.push(String(msg));
      this._emit('error', 'error', `runtime error: ${msg}`, {
        error: String(msg),
      });
    });
    rt.on('COMPILE_ERROR', (_target, error) => {
      this.errors.push(`compile error: ${error}`);
      this._emit('error', 'error', `compile error: ${error}`, {
        error: String(error),
      });
    });

    // The runtime has no hat-fired event, so observe the hat-start call
    // every hat block makes. Broadcasts carry their name; every other hat
    // logs its opcode + target at debug level so clicks, flags and edges
    // are visible in the timeline too.
    const startHats = rt.startHats.bind(rt);
    rt.startHats = (opcode, matchFields, target) => {
      if (opcode === 'event_whenbroadcastreceived' && matchFields) {
        const name = matchFields.BROADCAST_OPTION;
        if (name)
          this._emit('info', 'broadcast', `broadcast ${JSON.stringify(name)}`, {
            name,
          });
      } else if (opcode) {
        this._emit(
          'debug',
          'hat',
          `hat ${opcode} on ${target?.getName?.() ?? '?'}`,
          { opcode, target: target?.getName?.() ?? null },
        );
      }
      return startHats(opcode, matchFields, target);
    };

    // Coarse run boundaries, useful at debug level.
    rt.on('PROJECT_RUN_START', () =>
      this._emit('debug', 'run-start', 'scripts started running'),
    );
    rt.on('PROJECT_RUN_STOP', () =>
      this._emit('debug', 'run-stop', 'scripts finished running'),
    );

    // Push variable events: MONITORS_UPDATE fires every tick where a
    // monitored value changed, carrying the cloned MonitorState. Emit one
    // debug event per changed monitor (label = variable name for
    // data_variable opcodes) so watchers don't have to poll. Volume is
    // bounded by the monitor count, and the log cap already applies.
    rt.on(rt.constructor.MONITORS_UPDATE || 'MONITORS_UPDATE', (state) => {
      try {
        state?.valueSeq?.().forEach((m) => {
          const mon = m.toJS ? m.toJS() : m;
          const label = mon.opcode === 'data_variable' ? mon.params?.VARIABLE : mon.id;
          this._emit('debug', 'monitor', `monitor ${label} = ${JSON.stringify(mon.value)}`, {
            id: mon.id,
            label,
            value: mon.value,
          });
        });
      } catch {
        // A monitor that can't serialize must never break the run.
      }
    });
  }

  /**
   * Headless touching: scratch-vm's `isTouchingSprite` / `isTouchingEdge`
   * short-circuit to `false` when no renderer is attached (both check
   * `if (!this.renderer) return false`), so `touching <sprite>` and
   * `touching edge` blocks would never fire headless — arrows could never
   * hit an enemy, and nothing could ever die. Patch the RenderedTarget
   * prototype once per load with stage-coordinate fallbacks: distance-based
   * overlap for sprites (same size-scaled radius as the click shim) and a
   * stage-bounds check for the edge. The real renderer path still wins when
   * a renderer is ever attached. Clones only: the renderer checks every
   * clone of the sprite name, so we do too.
   * @private
   */
  _patchHeadlessTouching(vm) {
    const rt = vm.runtime;
    const sample = rt.targets.find((t) => !t.isStage);
    if (!sample) return;
    const proto = Object.getPrototypeOf(sample);
    if (proto.__headlessTouchingPatched) return;
    proto.__headlessTouchingPatched = true;

    const touches = (a, b) => {
      const ra = Math.max(12, (a.size / 100) * 24);
      const rb = Math.max(12, (b.size / 100) * 24);
      const r = ra + rb;
      return Math.abs(a.x - b.x) <= r && Math.abs(a.y - b.y) <= r;
    };

    // The stub renderer (attached by _attachHeadlessStubs) is flagged with
    // `__headlessStub`; when it's what a target holds, the REAL touching
    // implementations would call stub drawableTouching/isTouchingDrawables,
    // which always return false — projectiles would pass through forever.
    // So: use the fallbacks whenever the renderer is missing OR is the stub.
    const isStub = (r) => !!(r && r.__headlessStub);
    const origSprite = proto.isTouchingSprite;
    proto.isTouchingSprite = function (spriteName) {
      if (this.renderer && !isStub(this.renderer)) return origSprite.call(this, spriteName);
      const name = String(spriteName);
      return this.runtime.targets.some(
        (t) =>
          !t.isStage && !t.isOriginal && t.visible &&
          t.getName() === name && touches(this, t),
      );
    };

    const origEdge = proto.isTouchingEdge;
    proto.isTouchingEdge = function () {
      if (this.renderer && !isStub(this.renderer)) return origEdge.call(this);
      const r = Math.max(12, (this.size / 100) * 24);
      return (
        this.x - r < -240 ||
        this.x + r > 240 ||
        this.y - r < -180 ||
        this.y + r > 180
      );
    };
  }

  /**
   * Headless pen + sound stubs. scratch-vm's pen extension only touches the
   * renderer inside `penSkinId >= 0` guards (`scratch3_pen/index.js:197,523,
   * 539,562`) and `_getPenLayerID` only inits when `runtime.renderer` is
   * truthy — so a 7-method stub is enough for pen blocks to execute cleanly.
   * Pen *state* (`penDown`, color, size) lives on the sprite itself at
   * `_customState['Scratch.pen']` (`scratch3_pen/index.js:107-110`) and is
   * assertable with no pixels at all. Sound blocks no-op on the
   * `if (sprite.soundBank)` guard (`scratch3_sound.js:181,249,258`), so a
   * recording stub per sprite exercises the play path headless. The real
   * renderer/audio paths still win if ever attached.
   * @private
   */
  _attachHeadlessStubs(vm) {
    const rt = vm.runtime;
    /** Calls recorded for assertions: `{ pen: [...], sound: [...] }`. */
    this.stubCalls = { pen: [], sound: [] };
    /** 480x360 stage-coordinate pen canvas (RGBA, row-major). */
    this.penCanvas = { width: 480, height: 360, data: new Uint8ClampedArray(480 * 360 * 4) };
    const calls = this.stubCalls;
    const pen = this.penCanvas;
    // Stage (x,y) -> buffer index. y flips: stage +180 is row 0.
    const penDot = (x, y, r, g, b, a, diameter) => {
      const rad = Math.max(0.5, (diameter || 1) / 2);
      const cx = Math.round(x + 240);
      const cy = Math.round(180 - y);
      const R = Math.ceil(rad);
      for (let dy = -R; dy <= R; dy++) {
        for (let dx = -R; dx <= R; dx++) {
          if (dx * dx + dy * dy > rad * rad) continue;
          const px = cx + dx;
          const py = cy + dy;
          if (px < 0 || px >= pen.width || py < 0 || py >= pen.height) continue;
          const i = (py * pen.width + px) * 4;
          pen.data[i] = r;
          pen.data[i + 1] = g;
          pen.data[i + 2] = b;
          pen.data[i + 3] = a;
        }
      }
    };
    const penStroke = (x0, y0, x1, y1, r, g, b, a, diameter) => {
      // Bresenham over stage coords; dots at each step give round caps.
      const steps = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0), 1);
      for (let s = 0; s <= Math.ceil(steps); s++) {
        const t = s / Math.ceil(steps);
        penDot(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, r, g, b, a, diameter);
      }
    };
    const rgbaOf = (attrs) => {
      const c = attrs?.color4f || [0, 0, 0, 1];
      return [
        Math.round(c[0] * 255), Math.round(c[1] * 255),
        Math.round(c[2] * 255), Math.round((c[3] ?? 1) * 255),
      ];
    };
    if (!rt.renderer) {
      let skinId = 0;
      let drawableId = 0;
      rt.attachRenderer(Object.assign({
        // Flag for _patchHeadlessTouching: real touching impls delegate to
        // drawableID-based isTouchingDrawables, but originals built before
        // attachRenderer have drawableID === null, so that path always misses.
        // The patched fallbacks (stage-coordinate distance) are the reliable
        // headless semantics and key off this flag.
        __headlessStub: true,
        // --- skin/drawable lifecycle ---
        createPenSkin: () => ++skinId,
        createBitmapSkin: () => ++skinId,
        createSVGSkin: () => ++skinId,
        createTextSkin: () => ++skinId,
        updateBitmapSkin: () => {},
        updateSVGSkin: () => {},
        updateTextSkin: () => {},
        destroySkin: () => {},
        createDrawable: () => ++drawableId,
        destroyDrawable: () => {},
        updateDrawableSkinId: () => {},
        updateDrawablePosition: () => {},
        updateDrawableDirectionScale: () => {},
        updateDrawableVisible: () => {},
        updateDrawableEffect: () => {},
        updateDrawableOrder: () => {},
        setDrawableOrder: () => {},
        getDrawableOrder: () => 0,
        markDrawableAsNoninteractive: () => {},
        markSkinAsPrivate: () => {},
        setPrivateSkinAccess: () => {},
        setCustomFonts: () => {},
        // --- pen (record + rasterize into penCanvas) ---
        penClear: (skin) => {
          calls.pen.push({ op: 'clear', skin });
          pen.data.fill(0);
        },
        penPoint: (skin, attrs, x, y) => {
          calls.pen.push({ op: 'point', skin, x, y });
          const [r, g, b, a] = rgbaOf(attrs);
          penDot(x, y, r, g, b, a, attrs?.diameter);
        },
        penLine: (skin, attrs, x0, y0, x1, y1) => {
          calls.pen.push({ op: 'line', skin, x0, y0, x1, y1 });
          const [r, g, b, a] = rgbaOf(attrs);
          penStroke(x0, y0, x1, y1, r, g, b, a, attrs?.diameter);
        },
        penStamp: (skin, drawable) => calls.pen.push({ op: 'stamp', skin, drawable }),
        // --- sizing / bounds (cosmetic values; no pixels exist headless) ---
        getSkinSize: () => [100, 100],
        getNativeSize: () => [480, 360],
        getCurrentSkinSize: () => [100, 100],
        getSkinRotationCenter: () => [50, 50],
        getBounds: () => ({ left: -240, right: 240, top: 180, bottom: -180 }),
        getBoundsForBubble: () => ({ left: -240, right: 240, top: 180, bottom: -180 }),
        getFencedPositionOfDrawable: (id, pos) => pos,
        // --- picking/touching ---
        // The REAL isTouchingSprite/isTouchingEdge now run (they delegate
        // here once a renderer exists), so these must implement the same
        // stage-coordinate distance semantics as _patchHeadlessTouching's
        // fallbacks — NOT return false. getBounds above returns the full
        // stage, so isTouchingDrawables does the box test itself.
        pick: () => null,
        drawableTouching: () => false,
        // Color touching samples the pen raster: a sprite touches a color
        // when a matching pen pixel sits inside its size-scaled box.
        // `colorIsTouchingColor` passes (drawable, targetRgb, maskRgb) —
        // mask is approximated as "target color present" (documented).
        isTouchingColor: (drawableId, rgb, _maskRgb) => {
          const drawables = new Map();
          for (const t of rt.targets) {
            if (!t.isStage && t.drawableID != null) drawables.set(t.drawableID, t);
          }
          const t = drawables.get(drawableId);
          if (!t) return false;
          const r = Math.max(12, (t.size / 100) * 24);
          const c = Array.isArray(rgb) ? rgb.slice(0, 3) : [rgb, rgb, rgb];
          return this.penTouchesColor(t.x, t.y, r, c);
        },
        isTouchingDrawables: (aId, candidates) => {
          const drawables = new Map();
          for (const t of rt.targets) {
            if (!t.isStage && t.drawableID != null) drawables.set(t.drawableID, t);
          }
          const a = drawables.get(aId);
          if (!a) return false;
          const touches = (x, y) => {
            const ra = Math.max(12, (a.size / 100) * 24);
            const rb = Math.max(12, ((x.size || 100) / 100) * 24);
            const r = ra + rb;
            return Math.abs(a.x - x.x) <= r && Math.abs(a.y - x.y) <= r;
          };
          return (candidates || []).some((id) => {
            const b = drawables.get(id);
            return b && b.visible && touches(a, b);
          });
        },
        // --- frame orchestration ---
        setLayerGroupOrdering: () => {},
        setStageSize: () => {},
        setUseHighQualityRender: () => {},
        useHighQualityRender: false,
        draw: () => {},
        requestRedraw: () => {},
        offscreenTouching: false,
      }));
    }
    // Targets created during loadProject captured `this.renderer` BEFORE
    // attachRenderer above (constructor reads runtime.renderer once), so they
    // still hold null while runtime.renderer is now truthy. That asymmetry
    // crashes the say-bubble path: scratch3_looks._renderBubble passes its
    // `!runtime.renderer` guard, then getBoundsForBubble returns null because
    // the target's own renderer ref is null ("Cannot read properties of null
    // (reading 'right')"). Clones created later capture the renderer at
    // construction and are unaffected.
    for (const t of rt.targets) {
      if (t.isStage || !t.sprite) continue;
      if (!t.renderer) t.renderer = rt.renderer;
    }
    for (const t of rt.targets) {
      if (t.isStage || !t.sprite) continue;
      if (!t.sprite.soundBank) {
        t.sprite.soundBank = {
          playSound: (target, soundId) => {
            calls.sound.push({ op: 'play', target: target?.getName?.() ?? null, soundId });
            this._mixSound(target, soundId);
            return Promise.resolve();
          },
          stopAllSounds: (target) => {
            calls.sound.push({ op: 'stopAll', target: target?.getName?.() ?? null });
          },
          stop: (target, soundId) => {
            calls.sound.push({ op: 'stop', target: target?.getName?.() ?? null, soundId });
          },
          // sound-effect blocks call _syncEffectsForTarget -> setEffects;
          // missing it crashed green-flag on sound_cleareffects with
          // 'soundBank.setEffects is not a function'. No-op is correct
          // headless: effects are audio-only state.
          setEffects: () => {},
        };
      }
    }
    // Offline mix timeline: [{ atMs, pcm: Int16Array, rate }]. mixWav()
    // renders it to one 16-bit WAV. play() is scheduled at the project's
    // current timer (clock.projectTimer), so offsets are real.
    this._mix = [];
  }

  /**
   * Decode a WAV asset (PCM-16 mono, as generated by everything in this
   * repo) to `{ pcm: Int16Array, rate }`. ADPCM-encoded legacy assets
   * return null and are recorded but skipped in the mix (documented).
   * Pure RIFF parse — no AudioContext anywhere in this path.
   * @private
   */
  _decodeWav(bytes) {
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const str = (o, n) => String.fromCharCode(...bytes.slice(o, o + n));
    if (str(0, 4) !== 'RIFF' || str(8, 4) !== 'WAVE') return null;
    if (dv.getUint16(20, true) !== 1) return null; // PCM only
    const channels = dv.getUint16(22, true);
    const rate = dv.getUint32(24, true);
    const bits = dv.getUint16(34, true);
    if (bits !== 16) return null;
    // Find the data chunk (skip fact/LIST chunks).
    let off = 36;
    while (off + 8 <= bytes.length) {
      const id = str(off, 4);
      const size = dv.getUint32(off + 4, true);
      if (id === 'data') {
        const n = Math.floor(size / 2);
        const pcm = new Int16Array(n);
        for (let i = 0; i < n; i++) {
          pcm[i] = dv.getInt16(off + 8 + i * 2, true);
        }
        // Downmix to mono by averaging channel pairs.
        if (channels === 2) {
          const mono = new Int16Array(Math.floor(n / 2));
          for (let i = 0; i < mono.length; i++) {
            mono[i] = Math.round((pcm[i * 2] + pcm[i * 2 + 1]) / 2);
          }
          return { pcm: mono, rate };
        }
        return { pcm, rate };
      }
      off += 8 + size + (size % 2);
    }
    return null;
  }

  /**
   * Schedule a sound asset into the offline mix at the current project
   * timer. Volume/pitch effects read from the target (`soundEffects` is
   * audio-only state the stub doesn't model — documented approximate).
   * @private
   */
  _mixSound(target, soundId) {
    try {
      const sprite = target?.sprite;
      // With no audio engine, loadSound bails early and sounds keep only
      // their storage asset ({ assetId, dataFormat }); the raw WAV bytes
      // live in runtime.storage. Resolve through it, not sprite.sounds.
      const sounds = sprite?.sounds || [];
      const sound = sounds.find((s) => s.soundId === soundId || s.name === soundId || s.assetId === soundId);
      if (!sound) return;
      const storage = this.vm?.runtime?.storage;
      const asset = sound.asset || (storage && storage.get(sound.assetId));
      const raw = asset?.data;
      if (!raw) return;
      const bytes = raw instanceof Uint8Array ? raw : new Uint8Array(raw);
      const dec = this._decodeWav(bytes);
      if (!dec) return;
      const atMs = Math.round((this.vm?.runtime?.ioDevices?.clock?.projectTimer?.() ?? 0) * 1000);
      this._mix.push({ atMs, ...dec, name: sound.name });
    } catch {
      // A sound that can't be decoded must never break the run.
    }
  }

  /**
   * Render the offline mix to one 16-bit mono WAV (22050 Hz): sum each
   * scheduled sound at its play offset, hard-clip. Empty mix yields
   * 100 ms of silence so the file is always valid.
   *
   * @returns {{ rate: number, seconds: number, events: number, wavBase64: string }}
   */
  mixWav() {
    const rate = 22050;
    const events = this._mix.slice();
    let totalMs = 100;
    const decoded = [];
    for (const ev of events) {
      // Resample to the mix rate if needed (nearest-neighbor; fine for SFX).
      let { pcm, rate: src } = ev;
      if (src !== rate && src > 0) {
        const out = new Int16Array(Math.round((pcm.length * rate) / src));
        for (let i = 0; i < out.length; i++) {
          out[i] = pcm[Math.floor((i * src) / rate)] ?? 0;
        }
        pcm = out;
      }
      decoded.push({ atMs: ev.atMs, pcm });
      totalMs = Math.max(totalMs, ev.atMs + Math.round((pcm.length / rate) * 1000));
    }
    const total = Math.round((totalMs / 1000) * rate);
    const mix = new Int32Array(total);
    for (const { atMs, pcm } of decoded) {
      const start = Math.round((atMs / 1000) * rate);
      for (let i = 0; i < pcm.length && start + i < total; i++) {
        mix[start + i] += pcm[i];
      }
    }
    const wav = Buffer.alloc(44 + total * 2);
    wav.write('RIFF', 0);
    wav.writeUInt32LE(36 + total * 2, 4);
    wav.write('WAVE', 8);
    wav.write('fmt ', 12);
    wav.writeUInt32LE(16, 16);
    wav.writeUInt16LE(1, 20);
    wav.writeUInt16LE(1, 22);
    wav.writeUInt32LE(rate, 24);
    wav.writeUInt32LE(rate * 2, 28);
    wav.writeUInt16LE(2, 32);
    wav.writeUInt16LE(16, 34);
    wav.write('data', 36);
    wav.writeUInt32LE(total * 2, 40);
    for (let i = 0; i < total; i++) {
      const v = Math.max(-32768, Math.min(32767, mix[i]));
      wav.writeInt16LE(v, 44 + i * 2);
    }
    return {
      rate,
      seconds: Math.round((total / rate) * 100) / 100,
      events: events.length,
      wavBase64: wav.toString('base64'),
    };
  }

  /**
   * Press the green flag. Clears transient state (bubbles, pending question,
   * errors) so a subsequent `vm_state` reflects only this run. Does not advance
   * the VM on its own — call {@link run} to step it.
   */
  greenFlag() {
    const vm = this._vm();
    this.bubbles.clear();
    this.question = null;
    this.errors = [];
    this._emit('info', 'greenflag', 'green flag');
    vm.greenFlag();
    this._flush();
  }

  /** Stop every running script. */
  stop() {
    this._emit('info', 'stop', 'stop all');
    this._vm().stopAll();
    this._flush();
  }

  /**
   * Advance the VM frame by frame. By default it runs in real time (so `wait`,
   * timers and `glide` behave) until every script finishes or the budget is
   * spent. Set `paced: false` to step as fast as possible — faster, but
   * time-based blocks won't elapse correctly.
   *
   * @param {object} [opts]
   * @param {number} [opts.seconds] - Real-time budget. Default 10, capped at 60.
   * @param {number} [opts.frames] - Frame budget instead of `seconds`.
   * @param {boolean} [opts.untilIdle=true] - Stop early once no scripts run.
   * @param {boolean} [opts.paced=true] - Sleep one frame interval between steps.
   * @returns {Promise<object>} Frames run, whether the VM went idle, and state.
   */
  async run({ seconds, frames, untilIdle = true, paced = true } = {}) {
    const vm = this._vm();
    const rt = vm.runtime;
    const fps = rt.frameLoop?.framerate || 30;
    const interval = 1000 / fps;

    let budget;
    if (typeof frames === 'number') budget = frames;
    else budget = Math.ceil(Math.min(seconds ?? 10, 60) * fps);
    budget = Math.max(0, Math.min(budget, 60 * fps));

    // Monitor-update threads are perpetual (one per visible monitor) and
    // never represent game work — upstream's own `whenThreadsComplete`
    // helper filters them the same way. Idle + counts ignore them.
    const liveThreads = () =>
      rt.threads.filter((t) => !t.updateMonitor && !t.isKilled);
    let ran = 0;
    for (; ran < budget; ran++) {
      muted(() => rt._step());
      // Deliver events raised during the (muted) step now that stdout is live.
      this._flush();
      if (untilIdle && ran > 0 && liveThreads().length === 0) {
        ran++;
        break;
      }
      if (paced) await delay(interval);
    }
    // The event timeline since the previous `vm_run`: say/think, broadcasts,
    // question/answer, errors, … in the order they happened (a typical
    // load → green-flag → run flow yields load + greenflag + this run's events).
    const { events, dropped } = this.drainEvents();
    return {
      framesRun: ran,
      idle: liveThreads().length === 0,
      threadsRunning: liveThreads().length,
      events,
      ...(dropped ? { eventsDropped: dropped } : {}),
      ...this.summary(),
    };
  }

  /**
   * Feed input into the VM the way the editor would.
   *
   * @param {object} input
   * @param {Array<{ key: string, isDown?: boolean }>} [input.keys] - Key events.
   *   `key` is a Scratch key name ("space", "up arrow", "a", …). `isDown`
   *   defaults to a full press (down then up).
   * @param {number} [input.mouseX] - Stage x (-240..240) for the mouse.
   * @param {number} [input.mouseY] - Stage y (-180..180) for the mouse.
   * @param {boolean} [input.mouseDown] - Mouse button state.
   * @param {string} [input.answer] - Answer the pending `ask and wait`.
   * @returns {object} What was applied.
   */
  input({ keys, mouseX, mouseY, mouseDown, answer } = {}) {
    const vm = this._vm();
    const applied = {};

    if (Array.isArray(keys) && keys.length) {
      for (const { key, isDown } of keys) {
        if (isDown === undefined) {
          vm.postIOData('keyboard', { key, isDown: true });
          vm.postIOData('keyboard', { key, isDown: false });
        } else {
          vm.postIOData('keyboard', { key, isDown });
        }
      }
      applied.keys = keys;
    }

    if (
      mouseX !== undefined ||
      mouseY !== undefined ||
      mouseDown !== undefined
    ) {
      // The Scratch stage is always 480×360. The mouse handler maps canvas-space
      // coords back to stage coords using the canvas size we report, so feeding
      // a 480×360 canvas makes `data.x/y` a direct stage-coord translation.
      const W = 480;
      const H = 360;
      const data = { canvasWidth: W, canvasHeight: H };
      if (mouseX !== undefined) data.x = mouseX + W / 2;
      if (mouseY !== undefined) data.y = H / 2 - mouseY;
      if (mouseDown !== undefined) data.isDown = mouseDown;
      vm.postIOData('mouse', data);
      // Headless sprite clicks: without a renderer scratch-vm's own Mouse
      // device picks the Stage for every click, so `when this sprite clicked`
      // hats would never run. Fire them here on the same transitions the real
      // device uses: non-draggable targets on mouse-down, draggable on mouse-up.
      if (mouseX !== undefined) this._mouse.x = mouseX;
      if (mouseY !== undefined) this._mouse.y = mouseY;
      if (mouseDown !== undefined) this._mouse.down = mouseDown;
      if (
        this._mouse.down !== this._prevMouse.down &&
        (mouseX !== undefined || mouseY !== undefined)
      ) {
        const target = this._clickTargetAt(this._mouse.x, this._mouse.y);
        if (target && target !== vm.runtime.getTargetForStage()) {
          const press = this._mouse.down;
          if ((!target.draggable && press) || (target.draggable && !press))
            vm.runtime.startHats('event_whenthisspriteclicked', null, target);
        }
      }
      this._prevMouse = { ...this._mouse };
      applied.mouse = { mouseX, mouseY, mouseDown };
    }

    if (answer !== undefined) {
      // The VM resolves a pending `ask and wait` by emitting ANSWER.
      vm.runtime.emit('ANSWER', String(answer));
      this.question = null;
      applied.answer = String(answer);
      this._emit(
        'info',
        'answer',
        `answered: ${JSON.stringify(applied.answer)}`,
        {
          text: applied.answer,
        },
      );
    }

    this._flush();
    return applied;
  }

  /**
   * Pick the topmost visible sprite whose approximate bounding box contains
   * the stage point, or the Stage when nothing overlaps. Headless stand-in for
   * the renderer's `pick()`: scratch-vm's own Mouse device falls back to the
   * Stage without a renderer, which would make every sprite click a no-op.
   *
   * The hitbox is a generous size-scaled square around the sprite's anchor
   * (30 stage px at 100% size) — precise enough for clicking sprite centers,
   * which is the only thing a headless agent can do reliably.
   *
   * @param {number} mouseX - Stage x (-240..240).
   * @param {number} mouseY - Stage y (-180..180).
   * @returns {object} A scratch-vm target (sprite or Stage).
   * @private
   */
  _clickTargetAt(mouseX, mouseY) {
    const rt = this._vm().runtime;
    // Targets render in array order, later on top — so scan backwards.
    const sprites = rt.targets.filter((t) => !t.isStage && t.visible).reverse();
    for (const t of sprites) {
      const extent = Math.max(20, (t.size / 100) * 30);
      if (Math.abs(mouseX - t.x) <= extent && Math.abs(mouseY - t.y) <= extent)
        return t;
    }
    return rt.getTargetForStage();
  }

  /**
   * A structured snapshot of runtime state. The shape is designed for an agent
   * to assert against: read `variables`, `lists`, `monitors`, sprite positions
   * and `bubbles` rather than guessing from pixels.
   *
   * @returns {object}
   */
  summary() {
    const vm = this._vm();
    const rt = vm.runtime;

    const targets = rt.targets.map((t) => {
      const vars = Object.values(t.variables || {});
      const scalars = vars.filter((v) => v.type !== 'list');
      const lists = vars.filter((v) => v.type === 'list');
      const base = {
        name: t.getName?.() ?? t.id,
        isStage: !!t.isStage,
        isClone: !t.isOriginal,
        variables: Object.fromEntries(scalars.map((v) => [v.name, v.value])),
        lists: Object.fromEntries(lists.map((v) => [v.name, v.value])),
      };
      if (t.isStage) return base;
      const costume = t.sprite?.costumes?.[t.currentCostume];
      return {
        ...base,
        x: round(t.x),
        y: round(t.y),
        direction: round(t.direction),
        size: round(t.size),
        visible: t.visible,
        costume: costume?.name,
        costumeNumber: t.currentCostume + 1,
      };
    });

    return {
      targets,
      monitors: this._monitors(),
      bubbles: rt.targets.map((t) => bubbleOf(t)).filter(Boolean),
      question: this.question,
      threadsRunning: rt.threads.filter((t) => !t.updateMonitor && !t.isKilled).length,
      errors: this.errors.slice(),
    };
  }

  /**
   * Poll-and-diff variable watcher. Reads `target.variables` live (a plain
   * object per target — clones own their values, so per-clone watches work)
   * and compares against the last seen value for that watch key.
   *
   * @param {object} [opts]
   * @param {string} [opts.name] - Variable name to watch (default: all Stage scalars).
   * @param {string} [opts.target] - Sprite name, or omit for the Stage.
   * @returns {{ watches: object[] }} One entry per watched variable:
   *   `{ target, name, value, changed, previous }`.
   */
  watch({ name, target } = {}) {
    const rt = this._vm().runtime;
    const out = [];
    const lookup = (t) => {
      const vars = Object.values(t.variables || {}).filter((v) => v.type !== 'list');
      if (name) {
        const v = vars.find((v) => v.name === name);
        return v ? [[t, v]] : [];
      }
      return vars.map((v) => [t, v]);
    };
    const scope = target
      ? rt.targets.filter((t) => !t.isStage && t.getName?.() === target)
      : rt.targets.filter((t) => t.isStage);
    // Empty scope (unknown sprite name) returns [] — never silently fall
    // back to the Stage, which would report Stage vars for a typo'd target.
    for (const t of scope) {
      for (const [tt, v] of lookup(t)) {
        const key = `${tt.id}:${v.name}`;
        const prev = this._watches.get(key);
        const cur = { target: tt.getName?.() ?? tt.id, name: v.name, value: v.value };
        out.push({
          ...cur,
          changed: prev !== undefined && prev.value !== cur.value,
          previous: prev ? prev.value : null,
        });
        this._watches.set(key, cur);
      }
    }
    return { watches: out };
  }

  /**
   * Recorded pen/sound stub calls since load: `{ pen: [...], sound: [...] }`.
   * Pen state itself (`penDown`, color, size) lives on the sprite at
   * `_customState['Scratch.pen']` and is assertable with no pixels.
   *
   * @returns {{ pen: object[], sound: object[] }}
   */
  stubCallsOf() {
    return {
      pen: this.stubCalls.pen.slice(),
      sound: this.stubCalls.sound.slice(),
    };
  }

  /**
   * Pen canvas as PNG bytes (the 480x360 stage-coordinate raster built by
   * the pen stub). Deterministic given `setSeed` — stroke order is thread
   * order. Sprites/backdrops never render; this is pen strokes only.
   *
   * @returns {Promise<{ width: number, height: number, pngBase64: string,
   *   nonEmpty: number }>} `nonEmpty` = count of non-transparent pixels.
   */
  async penPng() {
    ensureDeps();
    this._vm(); // throw the standard "no project" error, not a null deref
    let sharpMod;
    try {
      sharpMod = require('sharp');
    } catch {
      throw new Error('sharp is not installed in the sidecar workspace');
    }
    const { width, height, data } = this.penCanvas;
    const png = await sharpMod(Buffer.from(data), {
      raw: { width, height, channels: 4 },
    }).png().toBuffer();
    let nonEmpty = 0;
    for (let i = 3; i < data.length; i += 4) {
      if (data[i] !== 0) nonEmpty++;
    }
    return {
      width, height,
      pngBase64: png.toString('base64'),
      nonEmpty,
    };
  }

  /**
   * Sample the pen canvas for a target RGB within a sprite's bounds box.
   * Backs `isTouchingColor`/`colorIsTouchingColor` headless: the stub
   * renderer's color methods delegate here instead of returning false.
   *
   * @param {number} x - Stage x center.
   * @param {number} y - Stage y center.
   * @param {number} radius - Stage px box half-size.
   * @param {number[]} rgb - [r, g, b] 0-255.
   * @param {number} [tolerance=30] - Per-channel tolerance.
   * @returns {boolean}
   */
  penTouchesColor(x, y, radius, rgb, tolerance = 30) {
    const { width, height, data } = this.penCanvas;
    const [tr, tg, tb] = rgb;
    const x0 = Math.max(0, Math.round(x + 240 - radius));
    const x1 = Math.min(width - 1, Math.round(x + 240 + radius));
    const y0 = Math.max(0, Math.round(180 - y - radius));
    const y1 = Math.min(height - 1, Math.round(180 - y + radius));
    for (let py = y0; py <= y1; py++) {
      for (let px = x0; px <= x1; px++) {
        const i = (py * width + px) * 4;
        if (data[i + 3] === 0) continue;
        if (Math.abs(data[i] - tr) <= tolerance &&
            Math.abs(data[i + 1] - tg) <= tolerance &&
            Math.abs(data[i + 2] - tb) <= tolerance) {
          return true;
        }
      }
    }
    return false;
  }

  /** Visible monitors (variable/list watchers, sensing readouts). @private */
  _monitors() {
    const state = this.vm.runtime.getMonitorState?.();
    if (!state) return [];
    const out = [];
    state.valueSeq().forEach((m) => {
      const mon = m.toJS ? m.toJS() : m;
      if (mon.visible === false) return;
      out.push({
        label: mon.opcode === 'data_variable' ? mon.params?.VARIABLE : mon.id,
        value: mon.value,
        mode: mon.mode,
      });
    });
    return out;
  }

  /**
   * Live thread inspector. Returns every thread the sequencer knows about:
   * which target it belongs to (sprite name, clone or original), which hat
   * started it, how deep its stack is, what block sits on top, and whether
   * it is running, yielded, waiting on a promise, or done.
   *
   * Unlocks clone counting: N threads with `isClone` on a given target name
   * after that target's spawn broadcast proves the clones exist.
   *
   * @returns {{ threads: object[], count: number }}
   */
  threads() {
    const rt = this._vm().runtime;
    const threads = rt.threads.map((t) => {
      const target = t.target;
      return {
        id: typeof t.getId === 'function' ? t.getId() : null,
        target: target?.getName?.() ?? target?.id ?? null,
        isClone: target ? !target.isOriginal : null,
        status: t.status,
        statusName:
          t.status === 0 ? 'running'
          : t.status === 1 ? 'promise-wait'
          : t.status === 2 ? 'yield'
          : t.status === 3 ? 'yield-tick'
          : t.status === 4 ? 'done'
          : String(t.status),
        topBlock: typeof t.peekStack === 'function' ? (t.peekStack() ?? null) : (t.topBlock ?? null),
        stackDepth: Array.isArray(t.stack) ? t.stack.length : null,
        isCompiled: !!t.isCompiled,
        updateMonitor: !!t.updateMonitor,
        isKilled: !!t.isKilled,
      };
    });
    return { threads, count: threads.length };
  }

  /**
   * Full monitor table (not just visible ones). Wraps
   * `runtime.getMonitorState()` so a variable can be read without pixels:
   * `label` resolves `data_variable` opcodes to the variable name.
   *
   * Unlocks watching a named variable or list, plus the vm_watch diff loop.
   *
   * @returns {{ monitors: object[] }}
   */
  monitors() {
    const state = this._vm().runtime.getMonitorState?.();
    if (!state) return { monitors: [] };
    const out = [];
    state.valueSeq().forEach((m) => {
      const mon = m.toJS ? m.toJS() : m;
      out.push({
        id: mon.id,
        label: mon.opcode === 'data_variable' ? mon.params?.VARIABLE : mon.id,
        opcode: mon.opcode,
        value: mon.value,
        mode: mon.mode,
        visible: mon.visible !== false,
        spriteName: mon.spriteName ?? null,
        targetId: mon.targetId ?? null,
      });
    });
    return { monitors: out };
  }

  /**
   * Step exactly one frame, then return before/after snapshots plus the
   * delta: which threads appeared and which events fired on that tick.
   *
   * Unlocks the MyHP per-clone proof: step one frame after a hit and diff
   * per-clone variables keyed by target id. Note the sequencer runs each
   * forever in a hat top-to-bottom, one iteration per tick — after one
   * frame only the *first* forever has run.
   *
   * @returns {Promise<object>}
   */
  async stepFrame() {
    const rt = this._vm().runtime;
    const beforeThreads = new Set(
      rt.threads.map((t) => (typeof t.getId === 'function' ? t.getId() : t)),
    );
    const beforeCount = rt.threads.length;
    muted(() => rt._step());
    this._flush();
    const after = this.summary();
    const newThreads = rt.threads.filter((t) => {
      const id = typeof t.getId === 'function' ? t.getId() : t;
      return !beforeThreads.has(id);
    }).map((t) => ({
      id: typeof t.getId === 'function' ? t.getId() : null,
      target: t.target?.getName?.() ?? t.target?.id ?? null,
      isClone: t.target ? !t.target.isOriginal : null,
      topBlock: typeof t.peekStack === 'function' ? (t.peekStack() ?? null) : null,
    }));
    const { events, dropped } = this.drainEvents();
    // Same monitor-thread filter as run()/summary(): killed + monitor
    // threads are sequencer bookkeeping, not game work.
    const live = (list) => list.filter((t) => !t.updateMonitor && !t.isKilled).length;
    return {
      framesRun: 1,
      idle: live(rt.threads) === 0,
      before: { threads: beforeCount },
      after,
      delta: { newThreads, newEvents: events },
      ...(dropped ? { eventsDropped: dropped } : {}),
    };
  }

  /**
   * Install (or restore) a deterministic PRNG for `operator_random` and
   * friends. scratch-vm has no seedable RNG — every random call site hits
   * the global `Math.random()` directly — so the only seam is to replace
   * the global with a mulberry32 stream. The compiled path bakes
   * `Math.random()` into generated code at *call time*, and this runtime
   * runs interpreted (`compilerOptions.enabled = false`), so the override
   * holds for both paths.
   *
   * Unlocks the deterministic-wave assertion: same seed → identical
   * `pick random` / lane sequences across runs.
   *
   * @param {number|null} seed - Integer seed, or null to restore `Math.random`.
   * @returns {{ seed: number|null, randomOverridden: boolean }}
   */
  setSeed(seed) {
    if (seed === null || seed === undefined) {
      if (this._realRandom) {
        Math.random = this._realRandom;
        this._realRandom = null;
      }
      return { seed: null, randomOverridden: false };
    }
    if (!this._realRandom) this._realRandom = Math.random;
    let s = (Number(seed) >>> 0) || 0x9e3779b9;
    Math.random = () => {
      s = (s + 0x6d2b79f5) | 0;
      let t = s;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    return { seed: Number(seed), randomOverridden: true };
  }

  /**
   * Run until a predicate fires, without a stdio round-trip per chunk.
   *
   * Predicates are data, not code: `{ varEquals?, broadcastSeen?,
   * threadsIdle? }` (see `vm_run_until` for the exact shapes). This is the
   * pushdown of the old Python `run_until()` loop — one call replaces N
   * `vm_run` polls, each of which returns a full state snapshot.
   *
   * Seconds are virtual: every frame advances the project timer by one frame
   * interval (`rt.currentStepTime`, 33ms at 30fps) instead of sleeping, so a
   * 10s wave costs 300 `_step()` calls with no wall-clock wait. `wait`,
   * timers and glides elapse per pushed millisecond; `pick random` and other
   * `Math.random` call sites are unaffected by the clock (use `vm_seed`).
   * Promise-based blocks (`say/think … for secs`, whose `setTimeout` is
   * wall-clock) still elapse in real time — the loop yields to the event
   * loop once per chunk so already-expired timers fire instead of starving.
   *
   * @param {object} [opts]
   * @param {number} [opts.seconds=10] - Virtual budget (max 60).
   * @param {number} [opts.chunkFrames=15] - Frames between predicate checks.
   * @param {object} [opts.until] - Fires when ANY entry matches (OR).
   * @param {object[]} [opts.until.varEquals] - `{target, name, value}` —
   *   fires when a live variable coerces equal (numeric when both sides
   *   parse, else string). `target` is a sprite name or "Stage" (default).
   * @param {string[]} [opts.until.broadcastSeen] - Broadcast names — fires
   *   when the timeline sees one since this call started.
   * @param {boolean} [opts.until.threadsIdle] - Fires when no live
   *   (non-monitor, non-killed) threads remain.
   * @returns {Promise<object>} Same shape as {@link run}, plus `reached`
   *   (bool) and `predicate` (the until object echoed back).
   */
  async runUntil({ seconds = 10, chunkFrames = 15, until = {} } = {}) {
    const vm = this._vm();
    const rt = vm.runtime;
    const fps = rt.frameLoop?.framerate || 30;
    const frameMs = rt.currentStepTime || 1000 / fps;
    const budget = Math.max(0, Math.min(Math.ceil(Math.min(seconds, 60) * fps), 60 * fps));
    const chunk = Math.max(1, Math.floor(chunkFrames) || 15);

    const liveThreads = () =>
      rt.threads.filter((t) => !t.updateMonitor && !t.isKilled);
    const varVal = (target, name) => {
      const scope = !target || target === 'Stage'
        ? rt.targets.filter((t) => t.isStage)
        : rt.targets.filter((t) => !t.isStage && t.getName?.() === target);
      for (const t of scope) {
        const v = Object.values(t.variables || {}).find(
          (x) => x.type !== 'list' && x.name === name,
        );
        if (v !== undefined) return v.value;
      }
      return undefined;
    };
    const numEq = (a, b) => {
      // Match the upstream convention: numeric when both sides parse, else
      // exact string. The VM reports numbers-as-strings after `change by`.
      const na = Number(a);
      const nb = Number(b);
      if (a !== '' && b !== '' && !Number.isNaN(na) && !Number.isNaN(nb)) return na === nb;
      return String(a) === String(b);
    };
    const matches = (broadcasts) => {
      for (const spec of until.varEquals || []) {
        const cur = varVal(spec.target, spec.name);
        if (cur !== undefined && numEq(cur, spec.value)) return true;
      }
      for (const name of until.broadcastSeen || []) {
        if (broadcasts.has(name)) return true;
      }
      if (until.threadsIdle && liveThreads().length === 0) return true;
      return false;
    };

    const seenBroadcasts = new Set();
    let ran = 0;
    let reached = false;
    for (; ran < budget; ran++) {
      muted(() => {
        rt._step();
        // Virtual clock: without a browser frame loop nothing advances
        // currentMSecs between _steps in a tight loop, so `wait 0.03`
        // would never elapse (all 300 frames same millisecond). Push the
        // clock one frame interval per step — virtual seconds, not wall.
        rt.currentMSecs = (rt.currentMSecs || Date.now()) + frameMs;
      });
      this._flush();
      // Promise-based blocks (say/think … for secs) resolve on the real
      // event loop's setTimeout, not the virtual clock. Yielding once per
      // chunk lets already-expired timers fire; pending ones keep their
      // threads alive (PROMISE_WAIT counts as live) so `threadsIdle` can't
      // fire early on a wave held open by a bubble timeout.
      if ((ran + 1) % chunk === 0 || ran + 1 === budget) {
        await new Promise((r) => setTimeout(r, 0));
        this._flush();
        if (matches(seenBroadcasts)) {
          reached = true;
          ran++;
          break;
        }
      }
      for (const e of this._eventLog) {
        if (e.type === 'broadcast' && e.name) seenBroadcasts.add(e.name);
      }
    }
    const { events, dropped } = this.drainEvents();
    return {
      framesRun: ran,
      idle: liveThreads().length === 0,
      reached,
      predicate: until,
      threadsRunning: liveThreads().length,
      events,
      ...(dropped ? { eventsDropped: dropped } : {}),
      ...this.summary(),
    };
  }

  /**
   * Set live VM state: variables, lists, sprite pose. Unlike the
   * project-editing `sb3_set_variable` (which edits JSON on disk), this
   * writes through to the RUNNING VM — fault injection mid-wave: grant
   * gold, drain a list to test the empty case, teleport a sprite.
   *
   * @param {object} [opts]
   * @param {Array<{target?: string, name: string, value}>} [opts.variables] -
   *   `target`: sprite name or "Stage" (default). Writes `v.value`.
   * @param {Array<{target?: string, name: string, items: unknown[]}>} [opts.lists]
   * @param {Array<{target: string, x?: number, y?: number, direction?: number,
   *   size?: number, visible?: boolean, costume?: string|number}>} [opts.sprites]
   * @returns {{ applied: object }} What changed (echo of matched writes).
   */
  poke({ variables = [], lists = [], sprites = [] } = {}) {
    const rt = this._vm().runtime;
    const applied = { variables: [], lists: [], sprites: [] };
    const findTargets = (name) =>
      !name || name === 'Stage'
        ? rt.targets.filter((t) => t.isStage)
        : rt.targets.filter((t) => !t.isStage && t.getName?.() === name);
    for (const spec of variables) {
      const scope = findTargets(spec.target);
      if (!scope.length) throw new Error(`poke: no target named "${spec.target}".`);
      let wrote = false;
      for (const t of scope) {
        const v = Object.values(t.variables || {}).find(
          (x) => x.type !== 'list' && x.name === spec.name,
        );
        if (v !== undefined) {
          v.value = spec.value;
          applied.variables.push({ target: t.getName?.() ?? t.id, name: spec.name, value: spec.value });
          wrote = true;
        }
      }
      if (!wrote) throw new Error(`poke: no variable "${spec.name}" on "${spec.target ?? 'Stage'}".`);
    }
    for (const spec of lists) {
      const scope = findTargets(spec.target);
      if (!scope.length) throw new Error(`poke: no target named "${spec.target}".`);
      if (!Array.isArray(spec.items)) throw new Error('poke: lists[].items must be an array.');
      let wrote = false;
      for (const t of scope) {
        const v = Object.values(t.variables || {}).find(
          (x) => x.type === 'list' && x.name === spec.name,
        );
        if (v !== undefined) {
          v.value = spec.items.slice();
          applied.lists.push({ target: t.getName?.() ?? t.id, name: spec.name, items: v.value.length });
          wrote = true;
        }
      }
      if (!wrote) throw new Error(`poke: no list "${spec.name}" on "${spec.target ?? 'Stage'}".`);
    }
    for (const spec of sprites) {
      const scope = rt.targets.filter((t) => !t.isStage && t.getName?.() === spec.target);
      if (!scope.length) throw new Error(`poke: no sprite named "${spec.target}".`);
      for (const t of scope) {
        const after = { target: spec.target };
        if (spec.x !== undefined) { t.setXY(spec.x, spec.y ?? t.y); after.x = spec.x; }
        else if (spec.y !== undefined) { t.setXY(t.x, spec.y); after.y = spec.y; }
        if (spec.direction !== undefined) { t.setDirection(spec.direction); after.direction = spec.direction; }
        if (spec.size !== undefined) { t.setSize(spec.size); after.size = spec.size; }
        if (spec.visible !== undefined) { t.setVisible(spec.visible); after.visible = spec.visible; }
        if (spec.costume !== undefined) {
          const idx = typeof spec.costume === 'number'
            ? spec.costume
            : (t.sprite?.costumes || []).findIndex((c) => c.name === spec.costume);
          if (idx < 0 || idx >= (t.sprite?.costumes || []).length) {
            throw new Error(`poke: no costume ${JSON.stringify(spec.costume)} on "${spec.target}".`);
          }
          t.setCostume(idx);
          after.costume = (t.sprite.costumes[idx] || {}).name ?? idx;
        }
        applied.sprites.push(after);
      }
    }
    return { applied };
  }

  /**
   * Every live clone in one view: who, where, and its sprite-local state.
   * `vm_threads` shows threads, `vm_state` shows originals — this is the
   * clone census for reasoning about live clones: which are aloft, their
   * per-clone sprite-locals, costume.
   *
   * @returns {{ clones: object[], count: number }}
   */
  clones() {
    const rt = this._vm().runtime;
    const clones = rt.targets
      .filter((t) => !t.isStage && !t.isOriginal)
      .map((t) => {
        const vars = Object.values(t.variables || {});
        const costume = t.sprite?.costumes?.[t.currentCostume];
        return {
          name: t.getName?.() ?? t.id,
          id: t.id,
          x: round(t.x),
          y: round(t.y),
          direction: round(t.direction),
          size: round(t.size),
          visible: t.visible,
          costume: costume?.name ?? null,
          variables: Object.fromEntries(
            vars.filter((v) => v.type !== 'list').map((v) => [v.name, v.value]),
          ),
        };
      });
    return { clones, count: clones.length };
  }

  /** Tear down the current VM, if any. */
  dispose() {
    if (this._realRandom) {
      Math.random = this._realRandom;
      this._realRandom = null;
    }
    if (this.vm) {
      try {
        muted(() => this.vm.quit?.());
      } catch {
        // best effort
      }
    }
    this.vm = null;
    this.bubbles.clear();
    this.question = null;
    this.errors = [];
    this._eventLog = [];
    this._droppedEvents = 0;
    this._notifyQueue = [];
    // Phase-3a state must not leak across loads: a stale mix would credit
    // the new project with the old project's sounds, stale watches would
    // report phantom diffs, and a stale canvas would tint pen PNGs.
    this.stubCalls = { pen: [], sound: [] };
    this._mix = [];
    this._watches = new Map();
    this.penCanvas = null;
  }
}

/** Round to one decimal place, leaving non-numbers untouched. */
const round = (n) => (typeof n === 'number' ? Math.round(n * 10) / 10 : n);

/**
 * The say/think bubble currently showing for a target, read from the live looks
 * state rather than accumulated SAY events — authoritative even when a bubble is
 * cleared by a path that doesn't re-emit SAY (e.g. `say … for secs` timing out
 * under the JIT compiler). Returns null when nothing is showing. The key is
 * scratch-vm's `Scratch3LooksBlocks.STATE_KEY` (`'Scratch.looks'`).
 *
 * @param {object} target - A scratch-vm `RenderedTarget`.
 * @returns {{ sprite: string, type: string, text: string } | null}
 */
const bubbleOf = (target) => {
  const state = target.getCustomState?.('Scratch.looks');
  if (!state || state.text === '' || state.text == null) return null;
  if (!target.isStage && !target.visible) return null; // hidden sprites show none
  return {
    sprite: target.getName?.() ?? target.id,
    type: state.type,
    text: String(state.text),
  };
};
