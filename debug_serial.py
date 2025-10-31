#!/usr/bin/env python3
"""Diagnostic script to debug serial communication with Q-Series sensor.

This script bypasses the library and directly tests pyserial to identify
why bytes aren't being transmitted.
"""

import sys
import time
import serial

def test_basic_write(port="/dev/ttyUSB0", baud=9600):
    """Test basic serial write without any library code."""
    print(f"\n{'='*70}")
    print(f"TEST 1: Basic pyserial write to {port} at {baud} baud")
    print(f"{'='*70}\n")

    # Open port with explicit flow control settings
    ser = serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.2,
        rtscts=False,    # Disable RTS/CTS
        dsrdtr=False,    # Disable DTR/DSR
        xonxoff=False,   # Disable XON/XOFF
    )

    print(f"Port opened: {ser.is_open}")
    print(f"Port name: {ser.port}")
    print(f"Baudrate: {ser.baudrate}")
    print(f"Timeout: {ser.timeout}")
    print(f"RTS/CTS: {ser.rtscts}")
    print(f"DTR/DSR: {ser.dsrdtr}")
    print(f"XON/XOFF: {ser.xonxoff}")
    print(f"Write timeout: {ser.write_timeout}")

    # Check control signals
    print(f"\nControl signals:")
    print(f"  CTS (Clear To Send): {ser.cts}")
    print(f"  DSR (Data Set Ready): {ser.dsr}")
    print(f"  RI (Ring Indicator): {ser.ri}")
    print(f"  CD (Carrier Detect): {ser.cd}")

    # Flush any existing data
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    # Read power-on banner
    print(f"\nWaiting for power-on banner from sensor...")
    time.sleep(1.0)

    lines_read = []
    for _ in range(20):  # Read up to 20 lines
        line = ser.readline()
        if line:
            try:
                decoded = line.decode('ascii', errors='replace').rstrip('\r\n')
                lines_read.append(decoded)
                print(f"  RX: {decoded!r}")
            except:
                print(f"  RX (raw): {line!r}")
        else:
            break

    if not lines_read:
        print("  ⚠️  No data received from sensor!")
        print("  Check physical connection and power.")

    # Now try to send ESC
    print(f"\n{'='*70}")
    print(f"Attempting to send ESC (0x1B) to enter menu...")
    print(f"{'='*70}\n")

    esc_byte = b'\x1b'
    print(f"Data to send: {esc_byte!r} (hex: {esc_byte.hex()})")

    # Attempt write
    print(f"Calling ser.write({esc_byte!r})...")
    bytes_written = ser.write(esc_byte)

    print(f"✓ write() returned: {bytes_written} bytes")
    print(f"  (If this is 0, the write was blocked or failed)")

    # Force flush
    print(f"\nCalling ser.flush() to force transmission...")
    ser.flush()
    print(f"✓ flush() completed")

    # Check output buffer
    print(f"\nOutput buffer status:")
    print(f"  out_waiting: {ser.out_waiting} bytes")

    # Wait for response
    print(f"\nWaiting up to 5 seconds for menu prompt...")
    start = time.time()
    response_lines = []

    while time.time() - start < 5.0:
        line = ser.readline()
        if line:
            try:
                decoded = line.decode('ascii', errors='replace').rstrip('\r\n')
                response_lines.append(decoded)
                print(f"  RX: {decoded!r}")

                if "Select the letter of the menu entry" in decoded:
                    print(f"\n✓✓✓ SUCCESS! Menu prompt received!")
                    break
            except:
                print(f"  RX (raw): {line!r}")

    if not response_lines:
        print(f"\n✗✗✗ FAILURE: No response received after ESC")
        print(f"This means:")
        print(f"  1. ESC byte was NOT transmitted (logic analyzer should confirm)")
        print(f"  2. OR sensor didn't recognize ESC (unlikely)")
        print(f"  3. OR sensor is not in a state that responds to ESC")

    ser.close()
    print(f"\nPort closed.")
    return bytes_written, len(response_lines) > 0


def test_with_timing_variations(port="/dev/ttyUSB0", baud=9600):
    """Test sending ESC at different times after port open."""
    print(f"\n{'='*70}")
    print(f"TEST 2: ESC transmission timing tests")
    print(f"{'='*70}\n")

    delays = [0.1, 0.5, 1.0, 2.0, 3.0]

    for delay in delays:
        print(f"\n--- Attempt: {delay}s delay after port open ---")

        ser = serial.Serial(
            port=port,
            baudrate=baud,
            timeout=0.2,
            rtscts=False,
            dsrdtr=False,
            xonxoff=False,
        )

        # Flush banner
        time.sleep(delay)
        ser.reset_input_buffer()

        # Send ESC
        esc = b'\x1b'
        written = ser.write(esc)
        ser.flush()

        print(f"  Wrote {written} bytes after {delay}s delay")

        # Check for response
        time.sleep(1.0)
        response = ser.read(100)

        if b"Select" in response or b"menu" in response:
            print(f"  ✓ Got menu response!")
            print(f"    Response: {response[:50]!r}...")
            ser.close()
            return True
        else:
            print(f"  ✗ No menu response (got {len(response)} bytes)")

        ser.close()
        time.sleep(0.5)  # Brief delay before next test

    return False


def test_alternative_commands(port="/dev/ttyUSB0", baud=9600):
    """Test sending '?' instead of ESC."""
    print(f"\n{'='*70}")
    print(f"TEST 3: Sending '?' (0x3F) instead of ESC")
    print(f"{'='*70}\n")

    ser = serial.Serial(
        port=port,
        baudrate=baud,
        timeout=0.2,
        rtscts=False,
        dsrdtr=False,
        xonxoff=False,
    )

    # Wait for banner
    time.sleep(2.0)
    ser.reset_input_buffer()

    # Send '?'
    question = b'?'
    print(f"Sending: {question!r} (hex: {question.hex()})")

    written = ser.write(question)
    ser.flush()

    print(f"Wrote {written} bytes")

    # Wait for response
    time.sleep(1.0)

    lines = []
    for _ in range(30):
        line = ser.readline()
        if line:
            decoded = line.decode('ascii', errors='replace').rstrip('\r\n')
            lines.append(decoded)
            print(f"  RX: {decoded!r}")
        else:
            break

    success = any("Select" in line or "menu" in line for line in lines)

    if success:
        print(f"\n✓ '?' triggered menu!")
    else:
        print(f"\n✗ '?' did not trigger menu")

    ser.close()
    return success


def check_library_transport(port="/dev/ttyUSB0", baud=9600):
    """Test the actual library Transport class."""
    print(f"\n{'='*70}")
    print(f"TEST 4: Testing library Transport class")
    print(f"{'='*70}\n")

    from q_sensor_lib.transport import Transport
    from q_sensor_lib import protocol

    print("Opening transport via library...")
    transport = Transport.open(port, baud)

    print(f"Transport created: {transport.is_open}")

    # Access underlying serial port
    ser = transport._port
    print(f"\nUnderlying pyserial object:")
    print(f"  Port: {ser.port}")
    print(f"  Baudrate: {ser.baudrate}")
    print(f"  RTS/CTS: {ser.rtscts}")
    print(f"  DTR/DSR: {ser.dsrdtr}")
    print(f"  XON/XOFF: {ser.xonxoff}")

    # Flush banner
    time.sleep(2.0)
    transport.flush_input()

    # Send ESC via library
    print(f"\nSending ESC via transport.write_bytes()...")
    transport.write_bytes(protocol.ESC)

    print(f"Checking if byte is stuck in output buffer...")
    print(f"  out_waiting: {ser.out_waiting}")

    # Wait for response
    print(f"\nWaiting for menu prompt...")
    response_lines = []

    for _ in range(30):
        line = transport.readline()
        if line:
            response_lines.append(line)
            print(f"  RX: {line!r}")

            if "Select the letter" in line:
                print(f"\n✓ Library successfully entered menu!")
                transport.close()
                return True

    print(f"\n✗ Library failed to enter menu")
    print(f"Total lines received: {len(response_lines)}")

    transport.close()
    return False


if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"

    print(f"\n{'#'*70}")
    print(f"# Q-Series Sensor Serial Communication Diagnostics")
    print(f"# Port: {port}")
    print(f"{'#'*70}")

    try:
        # Test 1: Basic write
        bytes_written, got_response = test_basic_write(port)

        if not got_response:
            print(f"\n⚠️  Basic ESC test failed. Trying alternatives...\n")

            # Test 2: Timing variations
            success = test_with_timing_variations(port)

            if not success:
                # Test 3: Try '?' instead
                success = test_alternative_commands(port)

        # Test 4: Library transport
        print(f"\n")
        lib_success = check_library_transport(port)

        # Final summary
        print(f"\n{'='*70}")
        print(f"DIAGNOSTIC SUMMARY")
        print(f"{'='*70}")
        print(f"If bytes_written=1 but no menu response:")
        print(f"  → ESC byte is being written to pyserial")
        print(f"  → But NOT transmitted on wire (check logic analyzer)")
        print(f"  → Likely cause: flow control, DTR/RTS behavior, or cable issue")
        print(f"\nIf bytes_written=0:")
        print(f"  → write() call is blocking or failing")
        print(f"  → Check port permissions, locks, or conflicts")
        print(f"\nNext steps:")
        print(f"  1. Check logic analyzer during Test 1")
        print(f"  2. Verify FTDI cable wiring (TX/RX not swapped)")
        print(f"  3. Try different baud rates")
        print(f"  4. Check dmesg for USB/serial errors")

    except Exception as e:
        print(f"\n✗✗✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
