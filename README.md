# 🖥️ LAN Video Conferencing System

A comprehensive LAN-based video conferencing application with real-time video/audio streaming, screen sharing, collaborative whiteboard, file transfer, and AI-powered gesture recognition.

## ✨ Features

- **🎥 Video Conferencing** - Real-time video streaming at 20 FPS with automatic layout adjustment
- **🎤 Audio Communication** - High-quality 16kHz audio with server-side mixing
- **🖥️ Screen Sharing** - Share your entire screen with all participants
- **🎨 Collaborative Whiteboard** - Real-time drawing with multiple tools (pen, shapes, lines)
- **📁 File Sharing** - Upload and download files with all participants
- **✋ Gesture Recognition** - AI-powered hand gesture detection using MediaPipe (👍, ✌️, 👋, ❤️, 👏)
- **🔐 Password Protected** - Auto-generated 4-character password for secure access
- **🌙 Dark/Light Theme** - Toggle between themes for comfortable viewing

## 🛠️ Tech Stack

- **GUI**: PyQt5
- **Video**: OpenCV
- **Audio**: PyAudio
- **Screen Capture**: mss
- **AI/Gesture**: MediaPipe
- **Networking**: TCP/UDP sockets

## 📦 Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Platform-specific (macOS)
brew install portaudio
pip install pyaudio
```

## 🚀 Usage

### Start Server (Host)
```bash
python server.py
```
Note the 4-character password displayed in the terminal.

### Start Client (Participants)
```bash
python client.py
```
Enter the server IP and password to join.

## 📋 Requirements

- Python 3.7+
- Webcam and microphone
- All participants on the same LAN network

## 🔌 Ports Used

| Feature | Protocol | Port |
|---------|----------|------|
| Control | TCP | 9000 |
| Video | UDP | 10000 |
| Audio | UDP | 11000 |
| Screen Share | TCP | 9001 |
| File Transfer | TCP | 9002 |

## 👥 Max Participants

Supports 50+ simultaneous participants on LAN.

## 📄 License

MIT License
