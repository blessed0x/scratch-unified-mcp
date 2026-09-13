PROJECT_EDITING = r"""
# Limitations of Scratch

---

> [!Note]
> Some of these (project-only) limitations apply to Scratch and TurboWarp; however, TurboWarp does provide some of its own config overrides to overcome these limits.

### Project (general)

-   Title maximum length: 100 characters.
-   project.json uncompressed file size limit: 5 MB
-   Individual asset (costume or sound) file size limit: 10 MB
-   There is no limit on sb3 file size.
-   There is no limit on block count but due to the project.json size limit, expect that 20000-30000 blocks can fit within it.
-   Project framerate: 30 FPS
-   Threads have a time limit of 500 ms. (encountered most often with custom blocks set to "run without screen refresh") [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/engine/sequencer.js#L62-L64)
-   The numbers (not strings) Infinity, \-Infinity, NaN, and \-0 can not be saved in JSON.
-   Scratch is mostly case-insensitive. Notably sprite and costume names are case-sensitive.
-   The project displays layers in the order: backdrop, video, pen, sprite. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/engine/stage-layering.js#L19-L27)

### Sprites

-   Sprites are visually snapped to a grid sized with 480x360 steps on the screen.
-   Size is limited by the current costume, when scaled it must not be visually larger than 150% the size of the stage (720x540) on any axis or smaller than 5 pixels on any axis. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/sprites/rendered-target.js#L373-L380)
-   Maximum number of clones at one time: 300 (limit shared by all sprites) [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/engine/runtime.js#L734-L736)
-   Sprites can not be positioned off-screen with a margin less than 15px still showing. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-render/src/RenderWebGL.js#L88)
-   Clones do not have an accessible unique ID and there is no native way to identify or share data between them. You need to implement this yourself with variables and lists. Note that those set to "for this sprite only" will store values unique to every clone/sprite, useful for an ID.

### Costumes/backdrops

-   Accepted image file formats: PNG and JPG. Note that if an edit is made to a costume, that costume will be saved as a lossless PNG. GIFs may also be imported, each frame will be added as a costume.
-   Bitmap maximum dimensions: 960x720 (using "half pixels").
-   Vector costumes get rasterized to dimensions no more than 2048x2048. This results in very large vector costumes appearing blurry. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-render/src/SVGSkin.js#L7)

### Looks

-   The color effect gives the sprite a minimum lightness and saturation. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-render/src/shaders/sprite.frag#L173-L178)
-   The "fisheye", "whirl", and "pixelate" effects are always relative to the center of the costume.
-   There is no "saturation" effect. Workarounds may involve multiple costumes or layering stamps or clones.

### Control

-   The "for each" loop compares the value in the repeat input every iteration. It is not stored. This is unintuitively unlike the "repeat" loop which will repeat the number of times given initially. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/blocks/scratch3_control.js#L94)
-   "Stop all" will not immediately stop broadcasts that were just scheduled to run, they will run for 1 frame and may trigger other scripts that can run longer than that.
-   "Stop other scripts in sprite" marks scheduled scripts to not run but will keep their order in the queue. They can still be reactivated within the same frame and will keep their order instead of being added to the end (and run last).

### Sensing

-   Touching color precision is limited to 5 bits red, 5 bits green, 4 bits blue. (this is only 14-bit color, much less than 24-bit RGB that is displayed by Scratch). [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-render/src/RenderWebGL.js#L67-L81)
-   The keys that can be detected are [here](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/io/keyboard.js). Special keys such as shift, control, and backspace can not be detected.
-   Left-click is the only mouse button that can be detected.
-   The mouse position is clamped to the edge of the screen and rounded. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/io/mouse.js#L60-L75)
-   Multi-touch (for touch-screens) is not supported. There is only 1 mouse pointer.
-   The timer only updates once per frame. For sub-frame timing, use the "days since 2000" block.
-   For the touching color blocks, the GPU will be used if there are more than 4000 pixels of the overlap in the touching area which may affect performance and change its behavior. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-render/src/RenderWebGL.js#L25)

### Operators

-   The "less-than", "greater-than", and "equals" blocks compare the inputs as floating point numbers if they can be casted. Long strings consisting of numeric characters may be compared in an unexpected way due to the numbers they are casted to (such as Infinity). This is also a problem with list "contains" and "item # of" blocks. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/util/cast.js#L122-L123)

### Events

-   Broadcasts can only run once per frame.
-   Broadcast receiver hats do not accept reporter blocks, the message selection is a field and you can only choose from the created messages.

### Variables and lists

-   Supported data types: booleans, double floats, strings. [Through hacks it's possible to utilize a few other types such as arrays.](https://scratch.mit.edu/projects/1048874723/)
-   List item limit: 200000 [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/blocks/scratch3_data.js#L252-L254)
-   There is no Scratch-imposed limit on string character count in variables and list items.
-   Lists are 1-indexed. Item numbers count from 1.
-   Neither variables or lists can be created at runtime, they must be created manually by the user. There is no way to place a reporter block in the variable or list name. Scratch also does not have dictionaries (or other similar collections).
-   Variable and list monitors are always displayed in front of everything on the screen. They are also not visible in saved thumbnails.

### Custom blocks

-   Custom blocks are not shared across all sprites. There must be a definition in every sprite that needs it.
-   Argument reporters are only usable within their own definition script and [always return 0 when run outside it](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/blocks/scratch3_procedures.js#L63). This includes clicking to run.
-   There is no return statement or reporter-shaped custom blocks. The typical workaround for returning a value is to set a variable in the custom block script. The "stop this script" block can be used to return early.

### Cloud variables

-   10 cloud variables per project. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/engine/runtime.js#L134)
-   Setting the value of any cloud variable has a shared average rate limit of 10 times per second. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-gui/src/lib/cloud-provider.js#L30-L32)
-   Variables may either store a floating point number (NaN and infinity excluded) or a string containing numerical digits 0-9, up to a length of 256 characters.
-   Calculated from the above limits, the maximum data transfer rate is approximately 1000 bytes/sec.
-   Users with "New Scratcher" status can not set cloud variables with values that other users can see.

### Sound

-   Accepted file formats: WAV and MP3. Stereo audio allowed. Note that if an edit is made to a sound, that sound will be saved as a mono WAV. [It may be downsampled to 22.05kHz if over the 10MB file size limit](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-gui/src/lib/audio/audio-util.js#L69-L90). I recommend making edits in software other than Scratch so the file is always transferred to Scratch as MP3 (where necessary). Other file formats may be added by editing the project externally but their support depends on the browser.
-   Volume range: 0 to 100% [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/blocks/scratch3_sound.js#L327)
-   Pitch effect range: -360 to 360 [Scratch Wiki](https://en.scratch-wiki.info/wiki/Sound_Effect#Pitch)
-   Pan effect range: -100 to 100 [Scratch Wiki](https://en.scratch-wiki.info/wiki/Sound_Effect#Pan_Left/Right)
-   Volume and effects are controlled per sprite/clone.
-   Sounds played with the "play sound" block can only be stopped with the "stop all" and "stop all sounds" blocks, there is no way to stop an individual sound here. Sounds played with the "play sound until done" block can be stopped with "stop other scripts in sprite" or by deleting the clone if it was running in a clone.
-   A sound can not be played multiple times simultaneously. Clones share the original sprite's sound file. The sound may be duplicated in the sounds editor (or added to a different sprite) if playing it simultaneously is desired.
-   The set/change volume and set/change effect blocks will cause a yield. [Source code (volume)](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/blocks/scratch3_sound.js#L331-L332) [Source code (effect)](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/blocks/scratch3_sound.js#L284-L285)
-   The first time the sound is played will have it briefly play at full volume. This is unavoidable. Other than ignoring it, the best way I found is to edit a short fade-in at the start of the audio file.
-   There may be a slight delay when the sound is first played as it gets loaded.
-   Sounds played with "play sound until done" are not guaranteed to loop seamlessly if played again immediately in a loop.

### Pen extension

-   The pen layer (internally called "skin") is in front of the stage but behind all sprites. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/engine/stage-layering.js#L23)
-   Dimensions: 480x360
-   There is no way to erase a stroke or only part of the pen layer. There is only an "erase all" block.
-   Pen "size" is its diameter in pixels and is limited to between 1 and 1200. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/extensions/scratch3_pen/index.js#L94-L102)
-   A pen size of exactly 1 or 3 will offset the pen position to the top-right by 0.5 steps to be "pixel-aligned". [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-render/src/PenSkin.js#L170)
-   An ARGB color value with full transparency is not possible as the way the value is handled always ignores alpha. If converted to a number (e.g. from hexadecimal), [the leading zeroes for alpha will be lost](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/util/cast.js#L100). If converted from a hex color code such as #FF3000, [alpha handling isn't implemented](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/util/color.js#L60-L69). If setting pen color, it is suggested to either use the set pen transparency effect after setting the RGB color or ensure the ARGB value never uses an alpha of 0 (possibly clamp to 1). Otherwise, conditionally draw pen strokes.
-   Every pen stroke is drawn independently of others; semi-transparent pixels from anti-aliasing or a semi-transparent pen color will be drawn visibly overlapping.

### Music extension

-   Duration in beats can only be between 0 and 100. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/extensions/scratch3_music/index.js#L703-L705)
-   Tempo can only be between 20 and 500. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/extensions/scratch3_music/index.js#L711-L713)
-   A maximum of 30 instrument sounds can be played simultaneously. [Source code](https://github.com/scratchfoundation/scratch-editor/blob/726fefa5bca3ff3e447c5bcb845150c628a81e7c/packages/scratch-vm/src/extensions/scratch3_music/index.js#L719-L721)

### Site

-   Usernames can only be chosen with a length between 3 and 20 (inclusive). Characters are limited to the english alphabet (case insensitive), numbers 0-9, the underscore \_ and dash \-. Note that there are some usernames that do not fit these requirements, they may have been set by the Scratch Team or created before some requirements were made. Most of the invalid ones are not in use, see for example [this topic of unusual usernames](https://scratch.mit.edu/discuss/topic/733836/). Generally, users can not change their username.

---

## Credits

- [Vadik1](https://scratch.mit.edu/users/Vadik1/)
- [Bambozzle](https://scratch.mit.edu/users/BamBozzle/)
- [awesome-llama](https://scratch.mit.edu/users/awesome-llama) / [awesome-llama](https://github.com/awesome-llama)
- [uukelele](https://scratch.mit.edu/users/uukelele) / [uukelele](https://github.com/uukelele)

---

# goboscript

Scratch projects are **not** edited here as raw `project.json`, and not as scratchblocks. They are written as **goboscript**: a text language that compiles to `.sb3`. You edit `.gs` source files with ordinary file tools, then compile.

goboscript is an external Rust binary. If a `project_*` tool reports it is missing, run `project_check_toolchain` and follow the install instructions.

## Workflow

1. `project_new` to scaffold, or `project_download` to decompile an existing Scratch project (by id, via `sb2gs`) into editable `.gs` source.
2. Edit the `.gs` files directly with your normal read/write/edit tools. There are deliberately no per-block MCP tools: the whole point of a text language is that a block edit is a text edit.
3. `project_build` to compile. **Use the tool, not the goboscript CLI.** A failed build raises with the compiler's diagnostics -- file, line, column and offending source -- so fix those and build again.
4. `project_summary` to check what you actually produced: per-sprite block and script counts, costumes, sounds, variables, layer order, embedded asset sizes, and warnings for unused or oversized assets and stale builds.
5. `project_save_to_cloud` to upload to scratch.mit.edu, with a public/private toggle.

## Looking things up

goboscript's names are **not** the Scratch block text. It is `switch_costume`, `change_x`, `set_ghost_effect`, `touching("sprite")`, `clone`, `play_sound_until_done`, `stop_this_script`. Guessing costs a build cycle.

Call **`project_goboscript_docs_help`** with no arguments for the full documentation index, then again with a page path to read it, for example `language/blocks/motion.md` or `language/reporters/sensing.md`. Statements live under `language/blocks/`, expressions under `language/reporters/`.

Things that are *not* statements, and catch people out: layer order is set in `goboscript.toml` via `layers = [...]`, not with a block; sprite defaults like `set_x` / `hide` are top-level statements outside any hat.

## Project layout

```
.
├── assets/
│   └── blank.svg
├── goboscript.toml
├── main.gs          # a sprite named "main"
└── stage.gs         # the Stage
```

Each `.gs` file **in the project root** is one sprite, named after the file. `stage.gs` is the Stage; you cannot name a sprite `Stage` or `stage`. `.gs` files in subdirectories are header files, not sprites.

`goboscript.toml` holds project config: `std`, `layers` (sprite layer order), `bitmap_resolution`, and the TurboWarp-only settings `frame_rate`, `max_clones`, `no_miscellaneous_limits`, `no_sprite_fencing`, `frame_interpolation`, `high_quality_pen`, `stage_width`, `stage_height`.

## Syntax basics

Whitespace is insignificant. Statements end in semicolons. Comments are `#` only.

```goboscript
0b111  0xFF  0o777  1024  3.141         # number literals
"Hello, \"World\"!\n\t\u1234"           # strings
true false                              # compile to 1 and 0
```

Non-boolean values in a boolean slot are auto-coerced: `if timer()` becomes `if timer() == 1`.

## Hat blocks

```goboscript
onflag { }                  # when green flag clicked
onkey "up arrow" { }        # when key pressed
onclick { }                 # when this sprite clicked
onbackdrop "name" { }       # when backdrop switches to
onloudness > 100 { }
ontimer > 100 { }
onclone { }                 # when I start as a clone
on "message name" { }       # when I receive
```

## Control flow

```goboscript
repeat n { }
until condition { }         # repeat until
forever { }
if c { } elif c2 { } else { }
```

## Variables

```goboscript
var score = 10;             # top-level declaration with an initial value
score = 5;                  # first assignment also declares
score++;  score--;
score += 1;  -=  *=  /=  //=  %=  &=      # &= is string join
show score;  hide score;    # monitor visibility
```

Variables are **for this sprite only** by default. Assigning in `stage.gs` makes them **for all sprites**. `local x = 0;` inside a procedure creates a procedure-scoped variable (compiled to `procname:x`) -- undefined behaviour in recursive or non-`nowarp` procedures.

## Lists

```goboscript
list items;
list items = [1, 2, 3];
list items "file.txt";              # one item per line, loaded at compile time

add v to items;
delete items[i];      delete items;
insert v at items[i];
items[i] = v;         v = items[i];
i = v in items;                     # index of
n = length items;
if v in items { }
v = items["random"];  v = items["last"];
```

## Custom blocks and functions

```goboscript
proc greet name = "world" {         # custom block, no return value
    say "Hello, " & $name;
}
nowarp proc fast a, b { }           # run without screen refresh
greet "aspizu";                     # positional
greet name: "aspizu";               # keyword

func add(x, y) {                    # function, returns a value
    return $x + $y;
}
```

Arguments are read as `$name`. Functions always run without screen refresh, must end in `return`, and must only be called from other non-refresh procedures. Custom blocks are per-sprite, as in Scratch.

## Costumes and sounds

```goboscript
costumes "assets/cat.svg";
costumes "assets/a.svg", "assets/b.svg";     # ordered as listed
costumes "assets/*.svg";                     # glob, sorted alphabetically
costumes "assets/cat.svg" as "kitty";        # rename
sounds "assets/meow.mp3";
```

Costume/sound names default to the filename without extension. Scratch accepts only PNG/JPG/SVG costumes and MP3/WAV sounds -- other formats fail to load with no warning.

## Sprite defaults

Top-level statements, outside any block:

```goboscript
set_x 100;  set_y 100;  set_size 100;
point_in_direction 90;  set_volume 100;
hide;
set_rotation_style_left_right;
set_rotation_style_all_around;
set_rotation_style_do_not_rotate;
```

## Beyond this summary

goboscript also has structs, enums, macros, a standard library, and operators Scratch lacks (`!=`, `>=`, `<=`, `//`, `not in`). Full documentation: <https://aspiz.uk/goboscript/docs>. Or you can use the documentation tool.

## Publishing

`project_save_to_cloud` compiles, uploads the code **and the assets** (Scratch stores them separately, so a code-only upload renders a broken project), creates the project on first save and reuses its id afterwards, and applies the `visibility` toggle.

TurboWarp-only extensions cannot be published to scratch.mit.edu. Scratch's own extensions are: `boost`, `coreExample`, `ev3`, `gdxfor`, `makeymakey`, `microbit`, `music`, `pen`, `text2speech`, `translate`, `videoSensing`, `wedo2`. `project_build` reports any others and marks the project `is_scratch_compatible: false`; publishing then fails until they are removed. Such projects still run in TurboWarp.
"""