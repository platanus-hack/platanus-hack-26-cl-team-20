"""Luces controlables del playground: foco SmallRig RC 100 por Bluetooth LE (app SmallGoGo, posiblemente Bluetooth
Mesh) o por DMX (su USB-C es alimentación PD + DMX con adaptador, no USB de datos; hace falta interfaz USB-DMX).

Flujo para una luz BLE nueva (sin protocolo público, como la SmallRig):
  1. `scan`      encuentra el equipo (nombre, dirección, RSSI).
  2. `dump`      lista servicios y características GATT (lectura, no cambia nada).
  3. `learn`     guarda en lights.json qué característica escribe qué (se descubre con el app oficial y un
                 sniffer, o probando valores en una característica writable con el foco a la vista).
  4. `set`       encendido, intensidad 0..1, temperatura K, color (si el modelo lo tiene) con el protocolo aprendido.
"""

from .ble import BleLight, scan, dump_gatt, LIGHTS_FILE

__all__ = ["BleLight", "scan", "dump_gatt", "LIGHTS_FILE"]
