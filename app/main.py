from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change to your frontend domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Room structure: room_id -> { "users": { user_id: WebSocket }, "host": user_id, "password": str, "locked": bool }
rooms: Dict[str, Dict[str, Any]] = {}

@app.websocket("/ws/{room_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, user_id: str):
    await websocket.accept()

    # Create room if doesn't exist
    if room_id not in rooms:
        rooms[room_id] = {
            "users": {},
            "host": user_id,
            "password": "",
            "locked": False
        }

    room = rooms[room_id]

    # Reject join if locked and not host
    if room["locked"] and user_id != room["host"]:
        await websocket.send_text(json.dumps({"type": "error", "message": "Room is locked"}))
        await websocket.close()
        return

    room["users"][user_id] = websocket

    await notify_users(room_id, {
        "type": "user-joined",
        "userId": user_id,
        "users": list(room["users"].keys()),
        "host": room["host"]
    })

    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)

            # Handle different message types
            msg_type = data.get("type")
            target = data.get("target")

            if msg_type in ["offer", "answer", "ice-candidate"]:
                # Direct relay to another user
                await send_to_user(room_id, target, msg)

            elif msg_type == "chat":
                await broadcast(room_id, {
                    "type": "chat",
                    "userId": user_id,
                    "message": data.get("message")
                })

            elif msg_type == "raise-hand":
                await broadcast(room_id, {
                    "type": "raise-hand",
                    "userId": user_id
                })

            elif msg_type == "set-password":
                if user_id == room["host"]:
                    room["password"] = data.get("password", "")
                    room["locked"] = True
                    await broadcast(room_id, {
                        "type": "room-locked",
                        "locked": True
                    })

            elif msg_type == "unlock-room":
                if user_id == room["host"]:
                    room["locked"] = False
                    await broadcast(room_id, {
                        "type": "room-locked",
                        "locked": False
                    })

            elif msg_type == "kick":
                if user_id == room["host"]:
                    kick_id = data.get("kickId")
                    if kick_id in room["users"]:
                        await send_to_user(room_id, kick_id, json.dumps({"type": "kicked"}))
                        await room["users"][kick_id].close()
                        del room["users"][kick_id]
                        await notify_users(room_id, {
                            "type": "user-left",
                            "userId": kick_id
                        })

            else:
                # Broadcast any other types (fallback)
                await broadcast(room_id, data, exclude=user_id)

    except WebSocketDisconnect:
        await handle_disconnect(room_id, user_id)

# Broadcast to all users
async def broadcast(room_id: str, message: Any, exclude: str = None):
    room = rooms.get(room_id)
    if not room:
        return

    if isinstance(message, str):
        text = message
    else:
        text = json.dumps(message)

    to_remove = []
    for uid, ws in room["users"].items():
        if uid == exclude:
            continue
        try:
            await ws.send_text(text)
        except:
            to_remove.append(uid)

    for uid in to_remove:
        del room["users"][uid]

# Send to a specific user
async def send_to_user(room_id: str, user_id: str, message: str):
    room = rooms.get(room_id)
    if room and user_id in room["users"]:
        try:
            await room["users"][user_id].send_text(message)
        except:
            del room["users"][user_id]

# Notify all about join/leave
async def notify_users(room_id: str, message: Dict[str, Any]):
    await broadcast(room_id, message)

# Handle disconnects
async def handle_disconnect(room_id: str, user_id: str):
    room = rooms.get(room_id)
    if room and user_id in room["users"]:
        del room["users"][user_id]

        # If host left, promote next user
        if user_id == room["host"]:
            if room["users"]:
                room["host"] = next(iter(room["users"]))
            else:
                del rooms[room_id]
                return

        await notify_users(room_id, {
            "type": "user-left",
            "userId": user_id,
            "host": room["host"]
        })
