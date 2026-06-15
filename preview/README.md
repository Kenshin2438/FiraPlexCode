# Quick preview

Three ways to see FiraPlexCode rendered, in order of "least friction" first.

## 1. Local web preview (no install)

```bash
# from the repo root, after `uv run python scripts/build_firaplex.py`
# and `uv run python scripts/patch_italics.py --variant all`
python -m http.server 8000
# then open http://localhost:8000/preview/ in a browser
```

The page loads the three variants directly from `build/stage2/` via
`@font-face` and renders code samples + ligatures + Nerd Font icon
glyphs. You can toggle family / size / ligatures live. Nothing is
installed system-wide; closing the tab releases the fonts.

## 2. Try in your terminal / editor without permanent install

### Linux (per-user font directory)

Linux honours fonts in `~/.local/share/fonts/`. Drop the four files of
the variant you want (Mono is recommended for terminals) into a
subdirectory and refresh the cache:

```bash
mkdir -p ~/.local/share/fonts/FiraPlexCode
cp build/stage2/NerdFontMono/*.ttf ~/.local/share/fonts/FiraPlexCode/
fc-cache -f ~/.local/share/fonts
fc-list | grep -i firaplex
```

Then in your terminal/editor set the font to **FiraPlexCode Nerd Font Mono**
(or NerdFont / NerdFontPropo for the other variants).

To remove later: `rm -rf ~/.local/share/fonts/FiraPlexCode && fc-cache -f`.

### macOS

Open Font Book, drag in the four `*.ttf` from `build/stage2/<variant>/`,
choose "Install for me only". To uninstall, select them in Font Book and
press delete.

### Windows

Right-click each `.ttf` and choose "Install for current user". Or copy
into `%LOCALAPPDATA%\Microsoft\Windows\Fonts\` (no admin needed).

## 3. VS Code / terminal config snippet

Once installed (Mono variant recommended for monospace contexts):

**VS Code `settings.json`:**

```jsonc
{
  "editor.fontFamily": "'FiraPlexCode Nerd Font Mono', Menlo, Consolas, monospace",
  "editor.fontLigatures": true,
  "terminal.integrated.fontFamily": "'FiraPlexCode Nerd Font Mono'"
}
```

**Alacritty (`alacritty.toml`):**

```toml
[font.normal]
family = "FiraPlexCode Nerd Font Mono"

[font.italic]
family = "FiraPlexCode Nerd Font Mono"
style = "Italic"
```

**WezTerm (`wezterm.lua`):**

```lua
config.font = wezterm.font_with_fallback {
  { family = "FiraPlexCode Nerd Font Mono", harfbuzz_features = { "calt=1", "liga=1" } },
}
```

## What to look for in the preview

| Region                 | What changed vs upstream                          |
|------------------------|---------------------------------------------------|
| Regular / Bold letters | Pure FiraCode Nerd Font - all ligatures intact    |
| Italic / BoldItalic    | IBM Plex Mono true italic - notice `f a r k x` flowing |
| `=>` `<=` `==` `!=`    | Should render as ligatures (FiraCode behaviour)   |
| Icon row               | Nerd Font PUA glyphs across all four styles       |
| Italic icon row        | Stage 2 successfully patched icons into italics   |
