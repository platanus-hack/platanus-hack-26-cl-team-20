"""Tests JS de la consola con el motor local (tests/console/*.test.mjs, `node --test`), más uno de punta a punta
contra el motor falso de Python. Sin node en el PATH se saltan. No usan navegador, cámara ni ventanas."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest
from test_fake_motor import TOKEN, fake_motor

NODE = shutil.which("node")
HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(NODE is None, reason="sin node en el PATH")
async def test_consola_motor_js():
    async with fake_motor() as (_, _base, url):
        env = {**os.environ, "PCX_FAKE_MOTOR_URL": url, "PCX_FAKE_MOTOR_TOKEN": TOKEN}
        proc = await asyncio.create_subprocess_exec(
            NODE,
            "--test",
            str(HERE / "console" / "motor.test.mjs"),
            cwd=HERE.parent,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 120)
    assert proc.returncode == 0, out.decode()
    print(out.decode())
