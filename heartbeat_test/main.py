import time

def run_forever():
    while True:
        print("Heartbeat... bot is alive.")
        time.sleep(10)

if __name__ == "__main__":
    run_forever()