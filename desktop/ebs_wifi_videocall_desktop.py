import os
import sys
import socket
import struct
import threading
import time
import subprocess
import queue
import winsound
from pathlib import Path

REQUIRED_PACKAGES = {
    "cv2": "opencv-python",
    "numpy": "numpy",
    "PIL": "Pillow",
    "sounddevice": "sounddevice",
    "mss": "mss",
    "customtkinter": "customtkinter",
    "qrcode": "qrcode[pil]",
}

def install_missing_packages():
    for import_name, pip_name in REQUIRED_PACKAGES.items():
        try:
            __import__(import_name)
        except ImportError:
            print(f"[i] Eksik paket yükleniyor: {pip_name}")
            subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])

install_missing_packages()

import cv2
import numpy as np
import sounddevice as sd
from PIL import Image
import mss
import customtkinter as ctk
import qrcode
from tkinter import filedialog, messagebox

VIDEO_RX_PORT = 12000      # APK -> PC kamera
AUDIO_RX_PORT = 12001      # APK -> PC mikrofon
VIDEO_TX_PORT = 12002      # PC -> APK kamera veya ekran
AUDIO_TX_PORT = 12003      # PC -> APK mikrofon
FILE_RX_PORT = 12100       # APK -> PC dosya
FILE_TX_PORT = 12101       # PC -> APK dosya
SIGNAL_PORT = 12200      # Arama bildirimi / cevap
APP_MAGIC = b"EBSVC1"
SAVE_DIR = Path("Gelen_Dosyalar")
SAVE_DIR.mkdir(exist_ok=True)
CONFIG_FILE = Path("ebs_videocall_config.txt")

DARK = "#071018"
CARD = "#101827"
CARD_2 = "#151f2f"
GREEN = "#00c781"
BLUE = "#0096ff"
RED = "#ff3b4f"
TEXT = "#eaf1fb"
MUTED = "#95a3b8"


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def safe_filename(name):
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name or f"EBS_FILE_{int(time.time())}"


class EBSVideoCallDesktop(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("EBS WiFi VideoCall Pro")
        self.geometry("1260x850")
        self.minsize(1080, 720)
        self.configure(fg_color=DARK)

        self.local_ip = get_local_ip()
        self.remote_ip = ctk.StringVar(value=self.load_saved_remote_ip())
        self.status = ctk.StringVar(value=f"Hazır | PC IP: {self.local_ip}")

        self.running = False
        self.send_mode = ctk.StringVar(value="camera")
        self.mic_enabled = ctk.BooleanVar(value=True)
        self.speaker_enabled = ctk.BooleanVar(value=True)
        self.remote_zoom = ctk.DoubleVar(value=1.0)
        self.fullscreen_window = None
        self.fullscreen_label = None
        self.fullscreen_photo = None
        self.last_remote_frame = None

        self.remote_video_queue = queue.Queue(maxsize=2)
        self.local_preview_queue = queue.Queue(maxsize=2)
        self.remote_last_seen = "Telefon bekleniyor"

        self.remote_photo = None
        self.local_photo = None
        self.qr_photo = None

        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(33, self.update_video_labels)
        threading.Thread(target=self.file_receive_server, daemon=True).start()
        threading.Thread(target=self.signal_server_loop, daemon=True).start()

    def build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color=CARD, corner_radius=22)
        top.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        top.grid_columnconfigure(1, weight=1)

        title_box = ctk.CTkFrame(top, fg_color="transparent")
        title_box.grid(row=0, column=0, padx=18, pady=14, sticky="w")
        ctk.CTkLabel(title_box, text="EBS VideoCall Pro", font=("Arial", 25, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(title_box, text="WhatsApp tarzı Wi‑Fi görüşme, dosya aktarımı ve ekran paylaşımı", font=("Arial", 12), text_color=MUTED).pack(anchor="w")

        ip_box = ctk.CTkFrame(top, fg_color=CARD_2, corner_radius=18)
        ip_box.grid(row=0, column=1, padx=12, pady=14, sticky="ew")
        ip_box.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(ip_box, text="PC IP / QR", text_color=MUTED, font=("Arial", 11, "bold")).grid(row=0, column=0, padx=(16, 8), pady=(10, 2), sticky="w")
        ctk.CTkLabel(ip_box, text=self.local_ip, text_color=GREEN, font=("Consolas", 17, "bold")).grid(row=1, column=0, padx=(16, 8), pady=(0, 12), sticky="w")
        ctk.CTkLabel(ip_box, text="Telefon IP", text_color=MUTED, font=("Arial", 11, "bold")).grid(row=0, column=1, padx=(16, 8), pady=(10, 2), sticky="w")
        self.phone_ip_label = ctk.CTkLabel(ip_box, text=self.remote_last_seen, text_color=TEXT, font=("Consolas", 15, "bold"))
        self.phone_ip_label.grid(row=1, column=1, padx=(16, 8), pady=(0, 12), sticky="w")

        self.qr_photo = self.make_qr_image(f"ebs-vc://{self.local_ip}:{VIDEO_RX_PORT}")
        qr_label = ctk.CTkLabel(top, text="", image=self.qr_photo)
        qr_label.grid(row=0, column=2, padx=(6, 8), pady=10, sticky="e")

        entry_box = ctk.CTkFrame(top, fg_color="transparent")
        entry_box.grid(row=0, column=3, padx=18, pady=14, sticky="e")
        self.remote_entry = ctk.CTkEntry(entry_box, textvariable=self.remote_ip, placeholder_text="Telefon IP manuel", width=180, height=38, corner_radius=15)
        self.remote_entry.pack(side="left", padx=(0, 8))
        ctk.CTkButton(entry_box, text="Başlat", fg_color=GREEN, hover_color="#00a86f", width=82, height=38, corner_radius=15, command=self.start_call_request).pack(side="left", padx=4)
        ctk.CTkButton(entry_box, text="Durdur", fg_color=RED, hover_color="#d92f42", width=82, height=38, corner_radius=15, command=self.stop).pack(side="left", padx=4)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=18, pady=8)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        remote_card = ctk.CTkFrame(body, fg_color=CARD, corner_radius=28)
        remote_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        remote_card.grid_rowconfigure(1, weight=1)
        remote_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(remote_card, text="Telefon Görüntüsü", text_color=TEXT, font=("Arial", 17, "bold")).grid(row=0, column=0, padx=20, pady=(16, 8), sticky="w")
        self.remote_video_label = ctk.CTkLabel(remote_card, text="Telefon görüntüsü bekleniyor", text_color=MUTED, fg_color="#000000", corner_radius=22)
        self.remote_video_label.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))

        side = ctk.CTkFrame(body, fg_color="transparent")
        side.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        side.grid_rowconfigure(0, weight=1)
        side.grid_columnconfigure(0, weight=1)

        local_card = ctk.CTkFrame(side, fg_color=CARD, corner_radius=28)
        local_card.grid(row=0, column=0, sticky="nsew")
        local_card.grid_rowconfigure(1, weight=1)
        local_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(local_card, text="Gönderilen Önizleme", text_color=TEXT, font=("Arial", 16, "bold")).grid(row=0, column=0, padx=18, pady=(16, 8), sticky="w")
        self.local_video_label = ctk.CTkLabel(local_card, text="PC kamera/ekran", text_color=MUTED, fg_color="#000000", corner_radius=22)
        self.local_video_label.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))

        controls = ctk.CTkFrame(self, fg_color=CARD, corner_radius=28)
        controls.grid(row=2, column=0, sticky="ew", padx=18, pady=(8, 18))
        controls.grid_columnconfigure(0, weight=1)

        left_controls = ctk.CTkFrame(controls, fg_color="transparent")
        left_controls.grid(row=0, column=0, padx=18, pady=16, sticky="w")
        ctk.CTkSegmentedButton(left_controls, values=["Kamera", "Ekran"], command=self.change_send_mode, width=190).pack(side="left", padx=(0, 12))
        ctk.CTkSwitch(left_controls, text="Mikrofon", variable=self.mic_enabled, text_color=TEXT, progress_color=GREEN).pack(side="left", padx=10)
        ctk.CTkSwitch(left_controls, text="Hoparlör", variable=self.speaker_enabled, text_color=TEXT, progress_color=GREEN).pack(side="left", padx=10)
        ctk.CTkButton(left_controls, text="Telefona Dosya Gönder", fg_color=BLUE, hover_color="#0078d7", height=36, corner_radius=14, command=self.send_file_to_phone).pack(side="left", padx=12)
        ctk.CTkButton(left_controls, text="Tam Ekran", fg_color="#7c3aed", hover_color="#6d28d9", height=36, corner_radius=14, command=self.open_fullscreen_remote).pack(side="left", padx=8)
        ctk.CTkLabel(left_controls, text="Zoom", text_color=MUTED).pack(side="left", padx=(12,4))
        ctk.CTkSlider(left_controls, from_=1.0, to=3.0, number_of_steps=20, variable=self.remote_zoom, width=120).pack(side="left", padx=4)

        self.status_label = ctk.CTkLabel(controls, textvariable=self.status, text_color=MUTED, font=("Consolas", 12))
        self.status_label.grid(row=0, column=1, padx=18, pady=16, sticky="e")

    def make_qr_image(self, data):
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((96, 96), Image.Resampling.LANCZOS)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(96, 96))

    def change_send_mode(self, value):
        self.send_mode.set("screen" if value == "Ekran" else "camera")
        self.status.set("PC ekranı gönderiliyor" if value == "Ekran" else "PC kamerası gönderiliyor")

    def start(self):
        remote = self.get_remote_ip()
        if remote:
            self.save_remote_ip(remote)
        if self.running:
            return
        self.running = True
        self.status.set(f"Aktif | APK'da QR okut veya PC IP gir: {self.local_ip}")
        threading.Thread(target=self.receive_video_loop, daemon=True).start()
        threading.Thread(target=self.receive_audio_loop, daemon=True).start()
        threading.Thread(target=self.send_video_loop, daemon=True).start()
        threading.Thread(target=self.send_audio_loop, daemon=True).start()

    def stop(self):
        self.running = False
        self.status.set("Görüşme durduruldu.")

    def on_close(self):
        self.running = False
        time.sleep(0.2)
        self.destroy()

    def receive_video_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", VIDEO_RX_PORT))
        sock.settimeout(1.0)
        buffer = {}
        while self.running:
            try:
                data, addr = sock.recvfrom(65535)
                self.remote_ip.set(addr[0])
                self.save_remote_ip(addr[0])
                self.after(0, lambda ip=addr[0]: self.phone_ip_label.configure(text=ip))
                if not data.startswith(APP_MAGIC) or len(data) < 18:
                    continue
                _, frame_id, total, index, payload_len = struct.unpack("!6sIHHI", data[:18])
                if total <= 0 or total > 128:
                    continue
                payload = data[18:18+payload_len]
                item = buffer.setdefault(frame_id, [None] * total)
                if index < total:
                    item[index] = payload
                if all(x is not None for x in item):
                    jpg = b"".join(item)
                    del buffer[frame_id]
                    arr = np.frombuffer(jpg, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is not None:
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        if self.remote_video_queue.full():
                            try: self.remote_video_queue.get_nowait()
                            except: pass
                        self.remote_video_queue.put(frame)
            except socket.timeout:
                continue
            except Exception as e:
                print("receive_video_loop:", e)
        sock.close()

    def receive_audio_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", AUDIO_RX_PORT))
        sock.settimeout(1.0)
        stream = None
        try:
            stream = sd.OutputStream(samplerate=16000, channels=1, dtype="int16", blocksize=320)
            stream.start()
            while self.running:
                try:
                    data, addr = sock.recvfrom(4096)
                    self.remote_ip.set(addr[0])
                    self.after(0, lambda ip=addr[0]: self.phone_ip_label.configure(text=ip))
                    if self.speaker_enabled.get() and data:
                        audio = np.frombuffer(data, dtype=np.int16)
                        if len(audio) > 0:
                            stream.write(audio)
                except socket.timeout:
                    continue
                except Exception as e:
                    print("receive_audio_loop:", e)
        finally:
            if stream:
                try: stream.stop(); stream.close()
                except: pass
            sock.close()

    def get_remote_ip(self):
        ip = self.remote_ip.get().strip()
        return ip if ip else None

    def save_remote_ip(self, ip):
        try:
            if ip:
                CONFIG_FILE.write_text(ip.strip(), encoding="utf-8")
        except Exception:
            pass

    def load_saved_remote_ip(self):
        try:
            if CONFIG_FILE.exists():
                return CONFIG_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        return ""

    def start_call_request(self):
        remote = self.get_remote_ip()
        if not remote:
            self.status.set("Önce telefon IP gir veya QR okut.")
            return
        self.save_remote_ip(remote)
        self.send_signal(remote, f"CALL|{self.local_ip}")
        self.status.set(f"Arama gönderildi → {remote}")
        self.start()

    def send_signal(self, remote, message):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(message.encode("utf-8"), (remote, SIGNAL_PORT))
            sock.close()
        except Exception as e:
            self.status.set(f"Arama sinyali gönderilemedi: {e}")

    def signal_server_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("", SIGNAL_PORT))
            sock.settimeout(1.0)
            while True:
                try:
                    data, addr = sock.recvfrom(1024)
                    msg = data.decode("utf-8", "ignore").strip()
                    from_ip = msg.split("|", 1)[1] if "|" in msg else addr[0]
                    if msg.startswith("CALL|"):
                        self.after(0, lambda ip=from_ip: self.show_incoming_call(ip))
                    elif msg.startswith("ACCEPT|"):
                        self.after(0, lambda ip=from_ip: self.status.set(f"Karşı taraf aramayı kabul etti: {ip}"))
                    elif msg.startswith("REJECT|"):
                        self.after(0, lambda ip=from_ip: self.status.set(f"Karşı taraf aramayı reddetti: {ip}"))
                except socket.timeout:
                    continue
                except Exception:
                    continue
        except Exception as e:
            self.status.set(f"Arama dinleyici hatası: {e}")
        finally:
            try: sock.close()
            except Exception: pass

    def play_incoming_ring(self):
        try:
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_LOOP)
        except Exception:
            pass

    def stop_incoming_ring(self):
        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    def show_incoming_call(self, from_ip):
        self.play_incoming_ring()
        try:
            answer = messagebox.askyesno("Gelen Görüntülü Arama", f"{from_ip} seni görüntülü arıyor. Kabul edilsin mi?")
        finally:
            self.stop_incoming_ring()
        if answer:
            self.remote_ip.set(from_ip)
            self.save_remote_ip(from_ip)
            self.send_signal(from_ip, f"ACCEPT|{self.local_ip}")
            self.status.set(f"Arama kabul edildi → {from_ip}")
            self.start()
        else:
            self.send_signal(from_ip, f"REJECT|{self.local_ip}")
            self.status.set(f"Arama reddedildi → {from_ip}")

    def send_video_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        frame_id = 0
        cap = None
        sct = mss.mss()
        while self.running:
            remote = self.get_remote_ip()
            if not remote:
                time.sleep(0.2)
                continue
            try:
                if self.send_mode.get() == "screen":
                    monitor = sct.monitors[1]
                    img = np.array(sct.grab(monitor))
                    frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                else:
                    if cap is None:
                        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
                    ok, frame = cap.read()
                    if not ok:
                        time.sleep(0.05)
                        continue

                frame = self.fit_frame(frame, 640, 360)
                preview = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                if self.local_preview_queue.full():
                    try: self.local_preview_queue.get_nowait()
                    except: pass
                self.local_preview_queue.put(preview)

                ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
                if not ok:
                    continue
                jpg = enc.tobytes()
                chunk_size = 1300
                total = (len(jpg) + chunk_size - 1) // chunk_size
                for idx in range(total):
                    chunk = jpg[idx*chunk_size:(idx+1)*chunk_size]
                    header = struct.pack("!6sIHHI", APP_MAGIC, frame_id, total, idx, len(chunk))
                    sock.sendto(header + chunk, (remote, VIDEO_TX_PORT))
                frame_id = (frame_id + 1) % 4294967295
                time.sleep(1/15)
            except Exception as e:
                print("send_video_loop:", e)
                time.sleep(0.2)
        if cap:
            cap.release()
        sock.close()

    def fit_frame(self, frame, width, height):
        h, w = frame.shape[:2]
        scale = min(width / w, height / h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        resized = cv2.resize(frame, (nw, nh))
        canvas = np.zeros((height, width, 3), dtype=np.uint8)
        x = (width - nw) // 2
        y = (height - nh) // 2
        canvas[y:y+nh, x:x+nw] = resized
        return canvas

    def send_audio_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        def callback(indata, frames, time_info, status):
            remote = self.get_remote_ip()
            if self.running and self.mic_enabled.get() and remote:
                try:
                    sock.sendto(indata.tobytes(), (remote, AUDIO_TX_PORT))
                except Exception:
                    pass
        stream = None
        try:
            stream = sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=320, callback=callback)
            stream.start()
            while self.running:
                time.sleep(0.2)
        finally:
            if stream:
                try: stream.stop(); stream.close()
                except: pass
            sock.close()

    def file_receive_server(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("", FILE_RX_PORT))
            server.listen(5)
            while True:
                conn, addr = server.accept()
                threading.Thread(target=self.receive_file_from_phone, args=(conn, addr), daemon=True).start()
        except Exception as e:
            self.status.set(f"Dosya sunucu hatası: {e}")

    def receive_file_from_phone(self, conn, addr):
        try:
            header = b""
            while b"\n" not in header:
                b = conn.recv(1)
                if not b:
                    break
                header += b
            file_name, file_size = header.decode("utf-8", "ignore").strip().split("|", 1)
            file_name = safe_filename(file_name)
            file_size = int(file_size)
            path = SAVE_DIR / file_name
            if path.exists():
                stem, ext = path.stem, path.suffix
                path = SAVE_DIR / f"{stem}_{int(time.time())}{ext}"
            received = 0
            with open(path, "wb") as f:
                while received < file_size:
                    chunk = conn.recv(min(16384, file_size - received))
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
            conn.close()
            self.remote_ip.set(addr[0])
            self.save_remote_ip(addr[0])
            self.after(0, lambda: self.phone_ip_label.configure(text=addr[0]))
            self.status.set(f"Telefondan dosya alındı: {path.name}")
        except Exception as e:
            self.status.set(f"Dosya alma hatası: {e}")
            try: conn.close()
            except: pass

    def send_file_to_phone(self):
        remote = self.get_remote_ip()
        if not remote:
            self.status.set("Önce telefon IP gir veya telefondan bağlantı başlat.")
            return
        path = filedialog.askopenfilename(title="Telefona gönderilecek dosyayı seç")
        if not path:
            return
        threading.Thread(target=self._send_file_to_phone_worker, args=(remote, Path(path)), daemon=True).start()

    def _send_file_to_phone_worker(self, remote, path: Path):
        try:
            size = path.stat().st_size
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect((remote, FILE_TX_PORT))
            sock.sendall(f"{path.name}|{size}\n".encode("utf-8"))
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(16384)
                    if not chunk:
                        break
                    sock.sendall(chunk)
            sock.close()
            self.status.set(f"Telefona dosya gönderildi: {path.name}")
        except Exception as e:
            self.status.set(f"Telefona dosya gönderme hatası: {e}")


    def zoom_frame(self, frame, zoom):
        if zoom <= 1.01:
            return frame
        h, w = frame.shape[:2]
        nw = max(1, int(w / zoom))
        nh = max(1, int(h / zoom))
        x = (w - nw) // 2
        y = (h - nh) // 2
        cropped = frame[y:y+nh, x:x+nw]
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)

    def open_fullscreen_remote(self):
        if self.fullscreen_window is not None:
            try:
                self.fullscreen_window.lift()
                return
            except Exception:
                self.fullscreen_window = None
        win = ctk.CTkToplevel(self)
        win.title("EBS Tam Ekran İzleme")
        win.configure(fg_color="#000000")
        win.attributes("-fullscreen", True)
        win.bind("<Escape>", lambda e: self.close_fullscreen_remote())
        self.fullscreen_label = ctk.CTkLabel(win, text="Görüntü bekleniyor", fg_color="#000000", text_color=MUTED)
        self.fullscreen_label.pack(fill="both", expand=True)
        bottom = ctk.CTkFrame(win, fg_color="#111827", corner_radius=20)
        bottom.place(relx=0.5, rely=0.94, anchor="center")
        ctk.CTkButton(bottom, text="Kapat", fg_color=RED, command=self.close_fullscreen_remote).pack(side="left", padx=12, pady=10)
        ctk.CTkLabel(bottom, text="Zoom", text_color=TEXT).pack(side="left", padx=8)
        ctk.CTkSlider(bottom, from_=1.0, to=4.0, number_of_steps=30, variable=self.remote_zoom, width=180).pack(side="left", padx=10)
        self.fullscreen_window = win
        self.update_fullscreen_frame()

    def close_fullscreen_remote(self):
        if self.fullscreen_window is not None:
            try: self.fullscreen_window.destroy()
            except Exception: pass
        self.fullscreen_window = None
        self.fullscreen_label = None

    def update_fullscreen_frame(self):
        if self.fullscreen_window is None or self.fullscreen_label is None or self.last_remote_frame is None:
            return
        try:
            w = max(800, self.fullscreen_window.winfo_width() - 30)
            h = max(600, self.fullscreen_window.winfo_height() - 30)
            frame = self.zoom_frame(self.last_remote_frame, max(1.0, float(self.remote_zoom.get())))
            self.fullscreen_photo = self.frame_to_ctk_image(frame, (w, h))
            self.fullscreen_label.configure(image=self.fullscreen_photo, text="")
        except Exception:
            pass

    def frame_to_ctk_image(self, frame, max_size):
        img = Image.fromarray(frame)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        return ctk.CTkImage(light_image=img, dark_image=img, size=img.size)

    def update_video_labels(self):
        try:
            remote_w = max(420, self.remote_video_label.winfo_width() - 40)
            remote_h = max(360, self.remote_video_label.winfo_height() - 40)
            local_w = max(280, self.local_video_label.winfo_width() - 30)
            local_h = max(180, self.local_video_label.winfo_height() - 30)

            while not self.remote_video_queue.empty():
                frame = self.remote_video_queue.get_nowait()
                self.last_remote_frame = frame
                z = max(1.0, float(self.remote_zoom.get()))
                self.remote_photo = self.frame_to_ctk_image(self.zoom_frame(frame, z), (remote_w, remote_h))
                self.remote_video_label.configure(image=self.remote_photo, text="")
                self.update_fullscreen_frame()

            while not self.local_preview_queue.empty():
                frame = self.local_preview_queue.get_nowait()
                self.local_photo = self.frame_to_ctk_image(frame, (local_w, local_h))
                self.local_video_label.configure(image=self.local_photo, text="")
        except Exception:
            pass
        self.after(33, self.update_video_labels)


if __name__ == "__main__":
    EBSVideoCallDesktop().mainloop()
