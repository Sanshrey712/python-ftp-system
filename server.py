"""
Lan Conference Server

Password Generated in server Terminal.
"""

import socket
import threading
import json
import struct
import os
import time
import logging
import random
import string
from collections import defaultdict, deque

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========= Configuration =========
TCP_PORT = 9000
VIDEO_UDP_PORT = 10000
AUDIO_UDP_PORT = 11000
SCREEN_TCP_PORT = 9001
FILE_TCP_PORT = 9002

MAX_UDP_SIZE = 65507
VIDEO_CHUNK_DATA = 1100
AUDIO_BUFFER_SIZE = 10
AUDIO_CHUNK_DURATION = 0.016

SERVER_HOST = '0.0.0.0'

# ===== Generate Server Password =====
def generate_password():
    """Generate a 4-digit alphanumeric password"""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(4))

SERVER_PASSWORD = generate_password()
logger.info(f"{'='*50}")
logger.info(f"🔐 SERVER PASSWORD: {SERVER_PASSWORD}")
logger.info(f"{'='*50}")

# ===== Global State =====
clients_lock = threading.Lock()
clients = {}
clients_by_name = {}

udp_video_targets = set()
udp_audio_targets = {}
audio_queues = defaultdict(lambda: deque(maxlen=AUDIO_BUFFER_SIZE))

screen_presenter = None
screen_viewers = {}
screen_lock = threading.Lock()

# Whiteboard state
whiteboard_state = {
    "strokes": [],
    "shapes": [],
    "texts": [],
    "version": 0
}
whiteboard_lock = threading.Lock()

# User colors for cursors
USER_COLORS = ["#4C88FF", "#27C48B", "#A47AFF", "#FFA24D", "#FF6B9D", "#00D9FF", "#FFD93D"]
user_color_index = 0

os.makedirs("server_files", exist_ok=True)

# ===== Framing Utilities =====
def write_msg(sock, obj):
    try:
        data = json.dumps(obj).encode('utf-8')
        length = struct.pack('!I', len(data))
        sock.sendall(length + data)
        return True
    except Exception as e:
        logger.debug(f"write_msg error: {e}")
        return False

def read_msg(sock):
    try:
        sock.settimeout(5.0)  # Reduce timeout to 5 seconds
        length_data = sock.recv(4)
        if not length_data or len(length_data) < 4:
            return None
        length = struct.unpack('!I', length_data)[0]
        if length > 50 * 1024 * 1024:
            return None
        data = b''
        while len(data) < length:
            chunk = sock.recv(min(16384, length - len(data)))
            if not chunk:
                return None
            data += chunk
        return json.loads(data.decode('utf-8'))
    except socket.timeout:
        logger.debug(f"read_msg timeout")
        return None
    except Exception as e:
        logger.debug(f"read_msg error: {e}")
        return Nones

# ===== TCP Helpers =====
def send_json(conn, obj):
    try:
        raw = (json.dumps(obj) + "\n").encode()
        conn.sendall(raw)
    except Exception as e:
        logger.debug(f"send_json error: {e}")
        pass

def broadcast_json(obj, exclude_conn=None):
    with clients_lock:
        for c in list(clients.keys()):
            try:
                if c is exclude_conn:
                    continue
                send_json(c, obj)
            except:
                cleanup_client(c)

def get_user_list():
    with clients_lock:
        user_list = []
        for info in clients.values():
            user_data = {
                "name": info["name"],
                "addr": f"{info['addr'][0]}",
                "color": info.get("color", "#4C88FF")
            }
            user_list.append(user_data)
            logger.info(f"[DEBUG] get_user_list: Adding user {user_data}")
        
        logger.info(f"[DEBUG] get_user_list returning {len(user_list)} users: {[u['name'] for u in user_list]}")
        return user_list

def cleanup_client(conn, name_from_info=None):
    info = None
    name = name_from_info
    client_ip = None

    with clients_lock:
        info = clients.pop(conn, None)
        if info:
            name = info.get("name", "unknown")
            client_ip = info.get("addr", ["unknown"])[0]
            clients_by_name.pop(name, None)

        try:
            if info.get("video_port"):
                udp_video_targets.discard((info["addr"][0], info["video_port"]))
            if info.get("audio_port"):
                udp_audio_targets.pop((info["addr"][0], info["audio_port"]), None)
        except Exception as e:
            logger.debug(f"cleanup_client error: {e}")
            pass

    if info:
        logger.info(f"[LEFT] {name} @ {info.get('addr')}")
        user_list = get_user_list()
        broadcast_json({"type": "user_list", "users": user_list})
        broadcast_json({"type": "leave", "name": name, "addr": client_ip})
    elif name_from_info:
        logger.info(f"[LEFT] {name_from_info} (redundant cleanup)")
        user_list = get_user_list()
        broadcast_json({"type": "user_list", "users": user_list})
        broadcast_json({"type": "leave", "name": name_from_info, "addr": None})

    try:
        conn.close()
    except:
        pass

# ===== TCP Control Handler =====
def handle_control(conn, addr):
    global user_color_index
    name = None
    try:
        buf = b""
        while True:
            data = conn.recv(4096)
            if not data:
                break

            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line:
                    continue

                try:
                    msg = json.loads(line.decode())
                except Exception as e:
                    logger.error(f"Bad JSON from {addr}: {e}")
                    continue

                mtype = msg.get("type")

                if mtype == "hello":
                    # Check password first
                    password = msg.get("password", "")
                    if password != SERVER_PASSWORD:
                        send_json(conn, {"type": "error", "message": "Invalid password", "auth_failed": True})
                        logger.warning(f"[AUTH] Failed login attempt from {addr}")
                        break
                    
                    name = msg.get("name", "anonymous")
                    vport = int(msg.get("video_port", 0) or 0)
                    aport = int(msg.get("audio_port", 0) or 0)

                    with clients_lock:
                        if name in clients_by_name:
                            send_json(conn, {"type": "error", "message": "Username already taken"})
                            name = None
                            break

                        # Assign color to user
                        user_color = USER_COLORS[user_color_index % len(USER_COLORS)]
                        user_color_index += 1

                        clients[conn] = {
                            "name": name,
                            "addr": addr,
                            "video_port": vport,
                            "audio_port": aport,
                            "last_seen": time.time(),
                            "color": user_color
                        }
                        clients_by_name[name] = conn

                        if vport:
                            udp_video_targets.add((addr[0], vport))
                        if aport:
                            udp_audio_targets[(addr[0], aport)] = (conn, name)

                    logger.info(f"[JOIN] {name} @ {addr} vport={vport} aport={aport} color={user_color}")

                    # Send whiteboard state to new user
                    with whiteboard_lock:
                        send_json(conn, {
                            "type": "whiteboard_sync",
                            "state": whiteboard_state
                        })

                    # Get and send user list
                    user_list = get_user_list()
                    logger.info(f"[DEBUG] Sending user list to {name}: {[u['name'] for u in user_list]}")

                    # Send to new user
                    send_json(conn, {"type": "user_list", "users": user_list})

                    # Broadcast join to others
                    broadcast_json({"type": "join", "name": name, "color": user_color}, exclude_conn=conn)

                    # Broadcast updated user list to all (including new user this time)
                    broadcast_json({"type": "user_list", "users": user_list})

                elif not name:
                    break

                elif mtype == "chat":
                    broadcast_json({"type": "chat", "from": name, "message": msg.get("message", "")})

                elif mtype == "private_chat":
                    target_name = msg.get("to")
                    message = msg.get("message", "")
                    target_conn = None

                    with clients_lock:
                        target_conn = clients_by_name.get(target_name)

                    if target_conn:
                        send_json(target_conn, {
                            "type": "private_chat",
                            "from": name,
                            "message": message
                        })
                        send_json(conn, {
                            "type": "private_chat_sent",
                            "to": target_name,
                            "message": message
                        })
                    else:
                        send_json(conn, {
                            "type": "error",
                            "message": f"User {target_name} not found"
                        })

                elif mtype == "gesture":
                    # Broadcast gesture to all users
                    gesture_type = msg.get("gesture_type")
                    broadcast_json({
                        "type": "gesture",
                        "from": name,
                        "gesture_type": gesture_type
                    }, exclude_conn=conn)
                    logger.info(f"[GESTURE] {name} -> {gesture_type}")

                elif mtype == "whiteboard_action":
                    # Handle whiteboard actions
                    action = msg.get("action")
                    
                    with whiteboard_lock:
                        if action == "draw":
                            whiteboard_state["strokes"].append(msg.get("data"))
                            whiteboard_state["version"] += 1
                        elif action == "shape":
                            whiteboard_state["shapes"].append(msg.get("data"))
                            whiteboard_state["version"] += 1
                        elif action == "text":
                            whiteboard_state["texts"].append(msg.get("data"))
                            whiteboard_state["version"] += 1
                        elif action == "erase":
                            erase_id = msg.get("erase_id")
                            whiteboard_state["strokes"] = [s for s in whiteboard_state["strokes"] if s.get("id") != erase_id]
                            whiteboard_state["shapes"] = [s for s in whiteboard_state["shapes"] if s.get("id") != erase_id]
                            whiteboard_state["texts"] = [t for t in whiteboard_state["texts"] if t.get("id") != erase_id]
                            whiteboard_state["version"] += 1
                        elif action == "clear":
                            whiteboard_state["strokes"] = []
                            whiteboard_state["shapes"] = []
                            whiteboard_state["texts"] = []
                            whiteboard_state["version"] += 1
                        elif action == "undo":
                            if whiteboard_state["strokes"]:
                                whiteboard_state["strokes"].pop()
                                whiteboard_state["version"] += 1
                            elif whiteboard_state["shapes"]:
                                whiteboard_state["shapes"].pop()
                                whiteboard_state["version"] += 1
                    
                    # Broadcast to all clients
                    broadcast_json({
                        "type": "whiteboard_action",
                        "from": name,
                        "action": action,
                        "data": msg.get("data"),
                        "erase_id": msg.get("erase_id"),
                        "version": whiteboard_state["version"]
                    }, exclude_conn=conn)

                elif mtype == "cursor_move":
                    # Broadcast cursor position to all users
                    broadcast_json({
                        "type": "cursor_move",
                        "from": name,
                        "x": msg.get("x"),
                        "y": msg.get("y"),
                        "color": clients[conn].get("color", "#4C88FF")
                    }, exclude_conn=conn)

                elif mtype == "present_start":
                    broadcast_json({"type": "present_start", "from": name}, exclude_conn=conn)

                elif mtype == "present_stop":
                    broadcast_json({"type": "present_stop", "from": name}, exclude_conn=conn)

                elif mtype == "bye":
                    break

    except Exception as e:
        logger.debug(f"Control handler exception: {e}")

    finally:
        cleanup_client(conn, name)

# ===== File Transfer Server =====
def file_transfer_server():
    logger.info(f"[FILE] Server listening on TCP {FILE_TCP_PORT}")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((SERVER_HOST, FILE_TCP_PORT))
    s.listen(10)

    try:
        while True:
            conn, addr = s.accept()
            threading.Thread(target=handle_file_transfer, args=(conn, addr), daemon=True).start()
    except Exception as e:
        logger.error(f"File transfer server error: {e}")
    finally:
        s.close()

def handle_file_transfer(conn, addr):
    try:
        data = conn.recv(4096)
        if not data:
            return

        msg = json.loads(data.decode())
        mtype = msg.get("type")

        if mtype == "file_upload":
            filename = msg.get("filename")
            size = msg.get("size")
            sender_name = msg.get("from")

            safe = os.path.basename(filename)
            dest = os.path.join("server_files", safe)

            conn.sendall(b"READY")

            remaining = size
            with open(dest, "wb") as f:
                while remaining > 0:
                    chunk = conn.recv(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)

            logger.info(f"[FILE] Received {safe} ({size} bytes) from {sender_name}")

            # Broadcast to ALL clients (including sender)
            broadcast_json({
                "type": "file_offer",
                "from": sender_name,
                "filename": safe,
                "size": size
            })

            conn.sendall(b"DONE")

        elif mtype == "file_download":
            filename = msg.get("filename")
            path = os.path.join("server_files", os.path.basename(filename))

            if not os.path.exists(path):
                conn.sendall(b"ERROR")
                return

            size = os.path.getsize(path)
            info = json.dumps({"size": size}).encode()
            conn.sendall(info + b"\n")

            conn.recv(10)

            with open(path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    conn.sendall(chunk)

            logger.info(f"[FILE] Sent {filename} to {addr}")

    except Exception as e:
        logger.error(f"[FILE] Transfer error: {e}")

    finally:
        try:
            conn.close()
        except:
            pass

# ===== Video Forwarder =====
video_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
video_sock.bind((SERVER_HOST, VIDEO_UDP_PORT))

def video_forwarder():
    logger.info(f"[VIDEO] Forwarder listening on UDP {VIDEO_UDP_PORT}")

    while True:
        try:
            data, addr = video_sock.recvfrom(MAX_UDP_SIZE)
            if len(data) < 8:
                continue

            try:
                src_ip_packed = socket.inet_aton(addr[0])
            except:
                src_ip_packed = b'\x00\x00\x00\x00'

            outpkt = src_ip_packed + data

            with clients_lock:
                targets = list(udp_video_targets)

            for tgt in targets:
                try:
                    video_sock.sendto(outpkt, tgt)
                except:
                    pass

        except Exception as e:
            logger.error(f"[VIDEO] Forwarder error: {e}")
            pass

# ===== Audio Receiver & Mixer =====
audio_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
audio_sock.bind((SERVER_HOST, AUDIO_UDP_PORT))

def audio_receiver():
    logger.info(f"[AUDIO] Receiver listening on UDP {AUDIO_UDP_PORT}")

    while True:
        try:
            data, addr = audio_sock.recvfrom(8192)
            if data:
                audio_queues[addr].append(data)
        except Exception as e:
            logger.debug(f"[AUDIO] Receiver error: {e}")
            pass

def audio_mixer():
    import numpy as np
    logger.info("[AUDIO] Mixer started - High-Precision Ticker & PLC enabled")

    last_good_audio = {}

    while True:
        tick_start = time.time()

        try:
            frames = []
            sources = []

            with clients_lock:
                current_audio_targets = set(udp_audio_targets.keys())
            known_ips = {ip for (ip, port) in current_audio_targets}

            for addr in list(audio_queues.keys()):
                q = audio_queues[addr]

                if addr[0] not in known_ips:
                    last_good_audio.pop(addr, None)
                    audio_queues.pop(addr, None)
                    continue

                if len(q) > 0:
                    try:
                        pkt = q.popleft()
                        frames.append(pkt)
                        sources.append(addr)
                        last_good_audio[addr] = pkt
                    except IndexError:
                        pass
                elif addr in last_good_audio:
                    frames.append(last_good_audio[addr])
                    sources.append(addr)

            for addr in list(last_good_audio.keys()):
                if addr[0] not in known_ips:
                    last_good_audio.pop(addr, None)

            if not frames:
                pass
            else:
                arrays = [np.frombuffer(f, dtype=np.int16) for f in frames if len(f) > 0 and len(f) % 2 == 0]

                if arrays:
                    minlen = min(a.shape[0] for a in arrays)
                    arrays = [a[:minlen] for a in arrays]

                    with clients_lock:
                        targets = list(udp_audio_targets.items())

                    for tgt_addr_tuple, (tgt_conn, tgt_name) in targets:
                        tgt_addr = (tgt_addr_tuple[0], tgt_addr_tuple[1])

                        tgt_arrays = []
                        for i, src_addr in enumerate(sources):
                            if src_addr[0] != tgt_addr_tuple[0]:
                                tgt_arrays.append(arrays[i])

                        if tgt_arrays:
                            stacked = np.vstack(tgt_arrays)
                            mixed = np.clip(np.mean(stacked, axis=0), -32768, 32767).astype(np.int16)
                            pkt = mixed.tobytes()

                            try:
                                audio_sock.sendto(pkt, tgt_addr)
                            except:
                                pass

        except Exception as e:
            logger.error(f"[AUDIO] Mixer error: {e}")

        tick_end = time.time()
        elapsed = tick_end - tick_start
        sleep_time = AUDIO_CHUNK_DURATION - elapsed

        if sleep_time > 0:
            time.sleep(sleep_time * 0.95)

# ===== Screen Sharing Relay =====
def screen_relay_server():
    logger.info(f"[SCREEN] Relay listening on TCP {SCREEN_TCP_PORT}")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((SERVER_HOST, SCREEN_TCP_PORT))
    s.listen(50)

    try:
        while True:
            conn, addr = s.accept()
            threading.Thread(target=handle_screen_connection, args=(conn, addr), daemon=True).start()
    except Exception as e:
        logger.error(f"Screen relay server error: {e}")
    finally:
        s.close()

def is_socket_alive(sock):
    """Check if a socket is still alive"""
    try:
        sock.setblocking(False)
        data = sock.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        return True
    except BlockingIOError:
        return True
    except (socket.error, OSError):
        return False
    finally:
        try:
            sock.setblocking(True)
        except:
            pass

def handle_screen_connection(conn, addr):
    global screen_presenter
    role = None
    
    try:
        conn.settimeout(5.0)
        role_msg = read_msg(conn)
        if not role_msg:
            logger.warning(f"[SCREEN] No role message from {addr}")
            conn.close()
            return
        
        role = role_msg.get("role")
        logger.info(f"[SCREEN] Connection from {addr} with role: {role}")
        
        if role == "presenter":
            with screen_lock:
                # ALWAYS clear any existing presenter - no checks
                if screen_presenter is not None:
                    old_sock = screen_presenter.get("socket")
                    logger.info(f"[SCREEN] Clearing existing presenter")
                    if old_sock:
                        try:
                            old_sock.close()
                        except:
                            pass
                    screen_presenter = None
                
                # Set new presenter
                screen_presenter = {"socket": conn, "addr": addr}
                logger.info(f"[SCREEN] New presenter set: {addr}")
            
            # Send OK response
            if not write_msg(conn, {"status": "ok"}):
                logger.error(f"[SCREEN] Failed to send OK to {addr}")
                with screen_lock:
                    screen_presenter = None
                conn.close()
                return
                
            logger.info(f"[SCREEN] Sent OK to presenter: {addr}")
            
            # Handle presenter frames
            try:
                conn.settimeout(2.0)  # Short timeout for quick disconnect detection
                frame_count = 0
                
                while True:
                    try:
                        frame = read_msg(conn)
                        if not frame:
                            logger.info(f"[SCREEN] Presenter {addr} - no frame")
                            break
                        
                        if frame.get("type") == "disconnect":
                            logger.info(f"[SCREEN] Presenter sent disconnect: {addr}")
                            break
                        
                        if frame.get("type") == "screen_frame":
                            broadcast_screen_frame(frame)
                            frame_count += 1
                            
                    except socket.timeout:
                        logger.info(f"[SCREEN] Presenter {addr} timeout after {frame_count} frames")
                        break
                        
            except Exception as e:
                logger.info(f"[SCREEN] Presenter exception: {e}")
            finally:
                with screen_lock:
                    if screen_presenter and screen_presenter.get("addr") == addr:
                        screen_presenter = None
                        logger.info(f"[SCREEN] Presenter cleared: {addr}")
                broadcast_screen_frame({"type": "present_stop"})
        
        elif role == "viewer":
            with screen_lock:
                screen_viewers[conn] = addr
                if screen_presenter:
                    write_msg(conn, {"status": "ok", "reason": "Presenter active"})
                else:
                    write_msg(conn, {"status": "ok", "reason": "No presenter"})
            
            logger.info(f"[SCREEN] Viewer connected: {addr}")
            
            try:
                conn.settimeout(None)
                while True:
                    data = conn.recv(1)
                    if not data:
                        break
            except:
                pass
            finally:
                with screen_lock:
                    screen_viewers.pop(conn, None)
                logger.info(f"[SCREEN] Viewer disconnected: {addr}")
    
    except Exception as e:
        logger.error(f"[SCREEN] Error from {addr}: {e}")
    finally:
        if role == "presenter":
            with screen_lock:
                if screen_presenter and screen_presenter.get("addr") == addr:
                    screen_presenter = None
                    logger.info(f"[SCREEN] Final cleanup: {addr}")
        elif role == "viewer":
            with screen_lock:
                screen_viewers.pop(conn, None)
        
        try:
            conn.close()
        except:
            pass

def broadcast_screen_frame(frame_data):
    with screen_lock:
        dead_viewers = []
        for viewer_sock, viewer_addr in list(screen_viewers.items()):
            if not write_msg(viewer_sock, frame_data):
                dead_viewers.append(viewer_sock)

        for dead in dead_viewers:
            try:
                dead.close()
            except:
                pass
            screen_viewers.pop(dead, None)

# ===== Main Server =====
def start_server():
    threading.Thread(target=video_forwarder, daemon=True).start()
    threading.Thread(target=audio_receiver, daemon=True).start()
    threading.Thread(target=audio_mixer, daemon=True).start()
    threading.Thread(target=screen_relay_server, daemon=True).start()
    threading.Thread(target=file_transfer_server, daemon=True).start()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((SERVER_HOST, TCP_PORT))
    s.listen(50)

    logger.info(f"[TCP] Control server listening on {SERVER_HOST}:{TCP_PORT}")

    try:
        while True:
            conn, addr = s.accept()
            threading.Thread(target=handle_control, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        logger.info("Shutting down server")
    finally:
        s.close()


if __name__ == "__main__":
    start_server()
