#!/bin/bash
# Genera una variante con el tool de imagen integrado de Codex CLI (plan ChatGPT, sin OPENAI_API_KEY).
# uso: gen_openai.sh <estilo>   (lee prompts/<estilo>.txt, escribe salidas/openai-<estilo>.png)
cd "$(dirname "$0")"; E="$1"; IN="${2:-entrada-norm.png}"
codex exec -s workspace-write -m gpt-5.6-terra -C "$PWD" -i "$IN" -- "Use your built-in image generation tool (image edit, with the attached image $IN as the input/reference image) exactly once to produce an edited version of the attached image, following the instructions below verbatim as the edit prompt. Request the output with the same aspect ratio as the input (2680x1420, about 1.89:1) if the tool allows; otherwise the closest landscape size. Then copy the generated image file into this directory as salidas/openai-$E.png (do not post-process, resize or crop it). Do not write code to fake the edit; if the image tool is unavailable, say so and stop. Finally report: the tool name used, the parameters you passed, the output pixel size, and the path of the original generated file.

$(cat prompts/$E.txt)" </dev/null > logs/openai-$E.log 2>&1
tail -25 logs/openai-$E.log | cut -c1-500; ls -la salidas
