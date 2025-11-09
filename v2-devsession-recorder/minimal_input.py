#!/usr/bin/env python3
"""
Hyperminimalist input handler for RecCli
No dependencies, full control over display
"""

import sys
import tty
import termios
import time
from typing import Optional

class MinimalInput:
    def __init__(self):
        self.buffer = []
        self.paste_threshold = 0.01  # 10ms between chars = paste

    def get_char(self) -> str:
        """Get a single character from terminal"""
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            char = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return char

    def get_input(self, prompt: str = "> ") -> str:
        """Get input with paste detection"""
        sys.stdout.write(prompt)
        sys.stdout.flush()

        buffer = []
        last_char_time = 0
        paste_detected = False
        char_count = 0

        while True:
            char = self.get_char()
            current_time = time.time()

            # Detect paste by timing
            if last_char_time and (current_time - last_char_time) < self.paste_threshold:
                if not paste_detected:
                    paste_detected = True
                    # Clear the prompt line
                    sys.stdout.write('\r' + ' ' * (len(prompt) + char_count) + '\r')
                    sys.stdout.write(prompt)
                    sys.stdout.flush()

            last_char_time = current_time

            # Handle special keys
            if char == '\r' or char == '\n':  # Enter
                break
            elif char == '\x7f':  # Backspace
                if buffer:
                    buffer.pop()
                    if not paste_detected:
                        sys.stdout.write('\b \b')
                        sys.stdout.flush()
                    char_count -= 1
            elif char == '\x03':  # Ctrl+C
                raise KeyboardInterrupt
            elif char == '\x04':  # Ctrl+D
                raise EOFError
            else:
                buffer.append(char)
                char_count += 1

                if not paste_detected:
                    # Normal typing - echo character
                    sys.stdout.write(char)
                    sys.stdout.flush()

        text = ''.join(buffer)

        if paste_detected:
            # Show annotation instead of content
            lines = text.count('\n') + 1
            chars = len(text)
            sys.stdout.write(f"[pasted +{lines} lines, {chars:,} chars]")

        sys.stdout.write('\n')
        sys.stdout.flush()

        return text

def test():
    """Test the minimal input handler"""
    print("Minimal Input Test")
    print("Type normally or paste content")
    print("Ctrl+C to exit\n")

    handler = MinimalInput()

    try:
        while True:
            text = handler.get_input("> ")
            print(f"Received: {len(text)} chars")
            if len(text) > 100:
                print("[Content hidden - too long]")
            else:
                print(f"Content: {text}")
            print("-" * 40)
    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == "__main__":
    test()