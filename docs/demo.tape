# Records docs/demo.gif with vhs (charmbracelet/vhs). Run from the repo root: vhs docs/demo.tape
# Offline: scripted models, no API key, no Docker.

Output docs/demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1100
Set Height 240
Set Padding 20
Set TypingSpeed 40ms

Hide
Type "uv sync --quiet && clear"
Enter
Sleep 5s
Show

Type "uv run agent-mesh eval"
Sleep 500ms
Enter
Sleep 8s
