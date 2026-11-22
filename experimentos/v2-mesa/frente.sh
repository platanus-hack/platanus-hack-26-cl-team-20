#!/bin/bash
# Deja la ventana de proyección de la mesa en pantalla completa y al frente de su proyector.
osascript -e 'tell application "System Events" to tell process "Google Chrome"
repeat with w in windows
if (name of w) starts with "WUI · proyector" then
if (value of attribute "AXFullScreen" of w) is false then set value of attribute "AXFullScreen" of w to true
end if
end repeat
end tell' >/dev/null 2>&1
sleep 2
osascript -e 'tell application "Google Chrome"
activate
repeat with w in windows
if (URL of active tab of w) contains "localhost:9217/projector.html" then set index of w to 1
end repeat
end tell' >/dev/null 2>&1
sleep 2
