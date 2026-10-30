# cdx

A visual `cd` for Windows cmd.exe. Type `cd` with no arguments to browse folders; `cd <path>` behaves like the normal `cd`.

## Install

Needs Python 3.8+ on PATH.

1. Clone this repo
2. Double-click `install.cmd` (or run it from a terminal)
3. Open a NEW cmd window and type `cd`

Uninstall: `install.cmd -Uninstall`

The installer copies the files to `%LOCALAPPDATA%\cdx` and adds an AutoRun entry that defines the `cd` macro.

## Keys

| Key | Action |
| --- | --- |
| type | filter folders live (top match pre-selected) |
| Up / Down | move the highlight |
| Enter / Right | open the highlighted folder |
| Ctrl+H | show/hide hidden + system folders |
| Ctrl+R | most used / recent folders (filter as usual; Ctrl+R, Left or Backspace on an empty filter goes back) |
| Backspace | delete a filter character; with an empty filter, go up a level |
| Left | go up a level |
| Ctrl+Enter / Ctrl+G | cd to the highlighted (or current) folder; on a `.bat` file: cd to its folder and run it |
| Esc / Ctrl+C | cancel, stay where you are |

Folders picked in the browser are remembered in `%LOCALAPPDATA%\cdx\history.json`. `cd <path>` typed directly is not recorded.
