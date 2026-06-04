#!/usr/bin/env python3
"""
Synapse CLI - Командная строка языка Synapse
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from synapse import run, compile_to_ast

def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <file.syn>")
        print("   or: python main.py -c 'code'")
        print("   or: python main.py --repl")
        sys.exit(1)

    arg = sys.argv[1]

    if arg == "--repl":
        run_repl()
    elif arg == "-c":
        code = sys.argv[2] if len(sys.argv) > 2 else input("Synapse> ")
        try:
            output = run(code)
            print(output)
        except Exception as e:
            print(f"Error: {e}")
    else:
        if not os.path.exists(arg):
            print(f"File not found: {arg}")
            sys.exit(1)
        with open(arg, "r", encoding="utf-8") as f:
            source = f.read()
        try:
            output = run(source)
            print(output)
        except Exception as e:
            print(f"Error: {e}")

def run_repl():
    print("╔══════════════════════════════════════╗")
    print("║  Synapse v0.7.0 - Язык для ИИ        ║")
    print("║  Type 'exit' to quit                 ║")
    print("╚══════════════════════════════════════╝")

    from synapse.interpreter import Interpreter
    interpreter = Interpreter()

    while True:
        try:
            line = input("synapse> ")
            if line.strip() in ["exit", "quit"]:
                break
            if not line.strip():
                continue

            output = run(line, interpreter)
            if output:
                print(output)
        except KeyboardInterrupt:
            print()
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main()
