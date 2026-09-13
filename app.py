from flask import Flask, jsonify, send_from_directory

from scanner import scan_market

app = Flask(__name__, static_folder="static")


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/scan")
def scan():
    return jsonify(scan_market())


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

