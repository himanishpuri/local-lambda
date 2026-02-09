import time

def handler(event):
    duration = event.get("sleep", 5)
    print(f"Sleeping for {duration} seconds...")
    time.sleep(duration)
    return {
        "message": f"Completed after {duration} seconds"
    }
