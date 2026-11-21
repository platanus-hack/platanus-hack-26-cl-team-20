"""Agente de voz «Playground» (ElevenLabs): cuenta los avances en vivo, recoge el feedback de Max y opera el playground.

Por defecto, conversación nativa en el Mac (voz.py: SDK elevenlabs + sounddevice, client tools locales, avances desde
avances.jsonl). Opcional: webhooks por un túnel propio hacia el puente local (server.py, tunel.py), con el mismo
patrón que la secretaria de Max pero agente, túnel, token y config propios. Ver README.md.
"""
