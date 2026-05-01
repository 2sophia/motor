# vhs tape file — generates assets/demo.gif for the README.
# Run from repo root:
#     PATH="$HOME/.local/bin:$PATH" vhs assets/demo/demo.tape

Output "assets/demo.gif"
Output "assets/demo.mp4"

Set FontSize 14
Set Width 1000
Set Height 560
Set TypingSpeed 25ms
Set PlaybackSpeed 1.0
Set Framerate 30
Set Theme "Catppuccin Mocha"

Hide
Type ".venv/bin/python assets/demo/demo_runner.py"
Enter
Sleep 3s
Show

# Header is on screen — pause briefly so viewers read it
Sleep 1500ms

Type "Read every sales-*.json and write outputs/report.md with the trend."
Sleep 600ms
Enter

# Wait for the run to finish (Glob + Read x3 + Write + answer + footer ~9s)
Sleep 11s

# Show output_files
Type "/files"
Enter
Sleep 2s

Type "/exit"
Enter
Sleep 500ms
