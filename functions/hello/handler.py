def handler(event):
    name = event.get("name", "world")
    return {
        "message": f"Hello, {name}!"
    }