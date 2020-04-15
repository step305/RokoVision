import os
import io
import time
import multiprocessing as mp
from queue import Empty
import picamera
from PIL import Image
from http import server
import socketserver
import numpy as np
import cv2

class QueueOutputMJPEG(object):
    def __init__(self, queue, finished):
        self.queue = queue
        self.finished = finished
        self.stream = io.BytesIO()

    def write(self, buf):
        if buf.startswith(b'\xff\xd8'):
            # New frame, put the last frame's data in the queue
            size = self.stream.tell()
            if size:
                self.stream.seek(0)
                if self.queue.empty():
                    self.queue.put(self.stream.read(size))
                self.stream.seek(0)
        self.stream.write(buf)

    def flush(self):
        self.queue.close()
        self.queue.join_thread()
        self.finished.set()

class QueueOutputH264(object):
    def __init__(self, queue, finished):
        self.queue = queue
        self.finished = finished
        self.stream = io.BytesIO()

    def write(self, buf):
        if True:
            size = self.stream.tell()
            if size:
                self.stream.seek(0)
                if self.queue.empty():
                    self.queue.put(self.stream.read(size))
                self.stream.seek(0)
        self.stream.write(buf)

    def flush(self):
        self.queue.close()
        self.queue.join_thread()
        self.finished.set()

def do_capture(queueH264, queueMJPEG, stopCap):
    print('Capture started')
    with picamera.PiCamera(sensor_mode=2) as camera:
        camera.resolution=(1280, 720)
        camera.framerate=15
        camera.video_stabilization = True
        camera.video_denoise = True
        camera.vflip = True
        camera.sharpness = 20
        camera.meter_mode = 'matrix'
        camera.awb_mode = 'auto'
        camera.saturation = 2
        camera.contrast = 10
        camera.drc_strength = 'high'
        camera.exposure_mode = 'antishake'
        camera.exposure_compensation = 3
        outputH264 = QueueOutputH264(queueH264, stopCap)
        outputMJPEG = QueueOutputMJPEG(queueMJPEG, stopCap)
        camera.start_recording(outputH264, format='h264', profile='high', intra_period=30, sps_timing=True, bitrate=4000000, quality=25, resize=(420,234))
        camera.start_recording(outputMJPEG, splitter_port=2, format='mjpeg', resize=(672,384))
        while not stopCap.wait(0): #camera.wait_recording(100)
            pass
        camera.stop_recording(splitter_port=2)
        camera.stop_recording()
        time.sleep(0.2)
        camera.close()

def do_detection(ImageQueue, RectQueue, finished):
    net = cv2.dnn.readNet('pedestrian-detection-adas-002.xml', 'pedestrian-detection-adas-002.bin')
    net.setPreferableTarget(cv2.dnn.DNN_TARGET_MYRIAD)
    st = time.monotonic()
    cnt = 1
    fps = 0
    FutureOuts = []
    ospid = os.getpid()
    while not finished.wait(0):
        stream = None
        try:
            stream = io.BytesIO(ImageQueue.get(False))
        except:
            pass
        if len(FutureOuts) == 3:
            stream = None
        if not stream is None:
            stream.seek(0)
            try:
                image = Image.open(stream).convert('RGB')
            except:
                pass
            cv_img = np.array(image)
            cv_img = cv_img[:, :, ::-1].copy()
            blob = cv2.dnn.blobFromImage(cv_img, 1.0, size=(672,384),\
                                         mean=(127.5, 127.5, 127.5), swapRB=False, crop=False)
            net.setInput(blob)
            FutureOuts.append(net.forwardAsync())
        while FutureOuts and FutureOuts[0].wait_for(0):
            out1 = FutureOuts[0].get()
            if cnt >= 20:
                fps = cnt/(time.monotonic() - st)
                st = time.monotonic()
                cnt = 1
                print('%d: Detecting at %FPS' % (ospid, fps))
            else:
                cnt += 1
            props = []
            for detection in out1.reshape(-1,7):
                inf = []
                obj_type = int(detection[1]-1)
                conf = float(detection[2])
                xmin = float(detection[3])
                ymin = float(detection[4])
                xmax = float(detection[5])
                ymax = float(detection[6])
                if conf > 0.6:
                    prop = {'coord': (xmin, ymin, xmax, ymax), 'type': obj_type, 'conf': conf}
                    props.append(prop)
            if RectQueue.empty():
                RectQueue.put(props)
            del FutureOuts[0]

class StreamingHandler(server.BaseHTTPRequestHandler):
    def do_GET(self):
        if '/data.html' in self.path:
            strprops = "ffffd9"
            if not self.server.DetectQueue.empty():
                props = self.server.DetectQueue.get(False)
                pcnt = 0
                for prop in props:
                    strprops += 'Coord = ({0:4f}, {1:4f}, {2:4f}, {3:4f}. ID = {4:d}\n'.format(
                        prop['coord'][0], prop['coord'][1], prop['coord'][2], prop['coord'][3], pcnt)
                    pcnt += 1
            strprops += "ffaaee"
            content = strprops.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        elif '/stream.mjpg' in self.path:
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            while self.server.MJPEGQueue.empty():
                pass
            buf = io.BytesIO(self.server.MJPEGQueue.get())
            try:
                st = time.monotonic()
                cnt = 1
                fps = 0
                ospid = os.getpid()
                while True:
                    if not self.server.MJPEGQueue.empty():
                        buf = io.BytesIO(self.server.MJPEGQueue.get(False))
                        if cnt >= 20:
                            fps = cnt/(time.monotonic() - st)
                            st = time.monotonic()
                            cnt = 1
                            print('%d: Streaming MJPEG at %dFPS' % (ospid, fps))
                        else:
                            cnt += 1
                        self.wfile.write(b'--FRAME\r\n')
                        self.send_header('Content-Type', 'image/jpeg')
                        self.send_header('Content-Length', len(buf.getvalue()))
                        self.end_headers()
                        self.wfile.write(buf.getvalue())
                        self.wfile.write(b'\r\r')
            except Exception as e:
                print('Removed streaming clients from MJPEG %s: %s', self.client_address, str(e))
        else:
            #self.send_response(200)
            #self.send_header('Age', 0)
            #self.send_header('Cache-Control', 'no-cache, private')
            #self.send_header('Pragma', 'no-cache')
            #self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            #self.end_headers()
            try:
                st2 = time.monotonic()
                cnt2 = 1
                fps2 = 0
                ospid2 = os.getpid()
                while True:
                    if not self.server.H264Queue.empty():
                        frame = io.BytesIO(self.server.H264Queue.get(False))
                        buf = frame
                        if cnt2 >= 20:
                            fps2 = cnt2/(time.monotonic() - st2)
                            st2 = time.monotonic()
                            cnt2 = 1
                            print('%d: Streaming H264 at %dFPS' % (ospid2, fps2))
                        else:
                            cnt2 += 1
                        self.wfile.write(buf.getvalue())
                        #self.wfile.write(b'\r\r')
            except Exception as e:
                print('Removed streaming clients from H264 %s: %s', self.client_address, str(e))
       # else:
       #     self.send_error(404)
       #     self.end_headers()

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def server_start(MJPEGQueue, H264Queue, DetectQueue, port, servstop):
    try:
        address = ('', port)
        server = StreamingServer(address, StreamingHandler)
        server.MJPEGQueue = MJPEGQueue
        server.DetectQueue = DetectQueue
        server.H264Queue = H264Queue
        print('Started server')
        server.serve_forever()
    finally:
        servstop.set()

if __name__ == '__main__':
    queueH264 = mp.Queue(1)
    queueMJPEG = mp.Queue(1)
    queueDetectRect = mp.Queue(1)
    stopCapture = mp.Event()
    queueProcessedLow = mp.Queue(1)
    queueProcessedHigh = mp.Queue(1)
    ServerStop = mp.Event()
    capture_proc = mp.Process(target=do_capture, args=(queueH264, queueMJPEG, stopCapture), daemon=True)
    server_proc = mp.Process(target=server_start, args=(queueMJPEG, queueH264, queueDetectRect, 8000, stopCapture), daemon=True)
    detect_proc = mp.Process(target=do_detection, args=(queueMJPEG, queueDetectRect, stopCapture), daemon=True)

    capture_proc.start()
    detect_proc.start()
    server_proc.start()

    while True:
        if stopCapture.is_set():
            stopCapture.set()
            time.sleep(0.1)
            capture_proc.terminate()
            server_proc.terminate()
            detect_proc.terminate()
            proccessing_proc_lores.terminate()
            break
        time.sleep(1)

