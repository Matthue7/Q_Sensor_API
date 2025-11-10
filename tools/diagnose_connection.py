"""Diagnose what happens during connection attempt."""

import serial
import time
import sys

def diagnose_connection(port="/dev/ttyUSB0"):
    """Show exactly what happens when we open port and send ESC."""

    print(f"\n=== Opening {port} ===")
    ser = serial.Serial(
        port=port,
        baudrate=9600,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.2,
        rtscts=False,
        dsrdtr=False,
        xonxoff=False
    )
    print(f"Port opened: {ser.is_open}")

    # Wait a moment and see what we receive (power-on banner)
    print("\n=== Waiting 1 second to see initial output ===")
    time.sleep(1.0)

    received = []
    while True:
        line = ser.readline()
        if not line:
            break
        decoded = line.decode('ascii', errors='replace').rstrip('\r\n')
        print(f"RX: {decoded!r}")
        received.append(decoded)

    print(f"\nReceived {len(received)} lines from power-on")

    # Now send ESC
    print("\n=== Sending ESC (0x1B) ===")
    esc_byte = b'\x1b'
    sent = ser.write(esc_byte)
    ser.flush()
    print(f"Sent {sent} bytes (ESC), flushed")

    # Wait for response
    print("\n=== Waiting 5 seconds for menu prompt ===")
    start = time.time()
    found_menu = False

    while time.time() - start < 5.0:
        line = ser.readline()
        if not line:
            continue

        decoded = line.decode('ascii', errors='replace').rstrip('\r\n')
        print(f"RX: {decoded!r}")

        if "Select the letter" in decoded:
            found_menu = True
            print("\n*** FOUND MENU PROMPT ***")
            break

    if not found_menu:
        print("\n*** MENU PROMPT NOT FOUND - ESC DID NOT WORK ***")
        print("\nPossible reasons:")
        print("1. Sensor is streaming and doesn't respond to ESC")
        print("2. Sensor needs different terminator with ESC")
        print("3. Sensor needs to be stopped first (different command)")
        print("4. DTR/RTS pin states are wrong")

    ser.close()
    print("\nPort closed")

if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
    diagnose_connection(port)
