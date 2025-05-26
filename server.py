from flask import Flask, request, render_template, jsonify, send_from_directory
import os
import base64
import uuid
import time
import json
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("print_server.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("PrintServer")

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = './uploads'
ALLOWED_EXTENSIONS = {'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Limit file size to 16MB

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# In-memory storage for pending commands and registered devices
pending_commands = {}  # Map of device_id -> list of commands
device_status = {}     # Map of device_id -> last seen timestamp
command_status = {}    # Map of command_id -> status information

# Function to check allowed file types
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin')
def admin():
    return render_template('admin.html', 
                          devices=device_status, 
                          commands=command_status,
                          pending=pending_commands)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No selected file"}), 400
    
    if not file or not allowed_file(file.filename):
        return jsonify({"status": "error", "message": "Invalid file format"}), 400

    # Get print options from form
    selected_pages = request.form.get('selected_pages', '')  # e.g., '1, 2-4'
    num_copies = int(request.form.get('num_copies', 1))  # default 1 copy
    layout = request.form.get('layout', 'portrait')  # 'portrait' or 'landscape'
    pages_per_sheet = int(request.form.get('pages_per_sheet', 1))  # Default 1 page per sheet
    upi_method = request.form.get('upi_method', '')
    
    # Validate payment method (this would be replaced with actual payment processing)
    if upi_method != 'success':
        return jsonify({"status": "error", "message": "Payment failed"}), 400
    
    try:
        # Generate unique filename
        filename = f"print_{int(time.time())}_{str(uuid.uuid4())[:8]}.pdf"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        # Read the file back as base64
        with open(file_path, 'rb') as f:
            pdf_data = base64.b64encode(f.read()).decode('utf-8')
        
        # Create command for all available printers
        command_id = str(uuid.uuid4())
        command = {
            "command_id": command_id,
            "type": "print",
            "pdf_data": pdf_data,
            "print_options": {
                "selected_pages": selected_pages,
                "num_copies": num_copies,
                "layout": layout,
                "pages_per_sheet": pages_per_sheet
            },
            "timestamp": datetime.now().isoformat(),
            "status": "pending"
        }
        
        # Select the most recently seen device (or the first one if none)
        target_device = None
        latest_time = 0
        
        for device_id, last_seen in device_status.items():
            if last_seen > latest_time:
                latest_time = last_seen
                target_device = device_id
        
        if not target_device and not device_status:
            # No devices seen yet, store command for the first device that registers
            if "default" not in pending_commands:
                pending_commands["default"] = []
            pending_commands["default"].append(command)
        else:
            # Send to the selected device
            device_id = target_device if target_device else list(device_status.keys())[0]
            if device_id not in pending_commands:
                pending_commands[device_id] = []
            pending_commands[device_id].append(command)
        
        # Store command status
        command_status[command_id] = {
            "id": command_id,
            "device_id": target_device if target_device else "pending assignment",
            "timestamp": datetime.now().isoformat(),
            "status": "pending",
            "file": filename
        }
        
        # Clean up the original file since we've encoded it
        os.remove(file_path)
        
        logger.info(f"Print job {command_id} submitted successfully")
        return jsonify({"status": "success", "message": "Print job submitted"})
    
    except Exception as e:
        logger.error(f"Error processing upload: {str(e)}")
        return jsonify({"status": "error", "message": f"Server error: {str(e)}"}), 500

@app.route('/api/check_commands', methods=['POST'])
def check_commands():
    data = request.json
    if not data or 'device_id' not in data:
        return jsonify({"error": "Missing device_id"}), 400
    
    device_id = data['device_id']
    
    # Update device status
    device_status[device_id] = time.time()
    
    # Check if there are any pending commands for this device
    commands_to_send = []
    
    # Check device-specific commands
    if device_id in pending_commands and pending_commands[device_id]:
        commands_to_send.extend(pending_commands[device_id])
        pending_commands[device_id] = []  # Clear the commands being sent
    
    # Check for default commands (device not specified)
    if "default" in pending_commands and pending_commands["default"]:
        # Assign default commands to this device
        for command in pending_commands["default"]:
            command_id = command["command_id"]
            if command_id in command_status:
                command_status[command_id]["device_id"] = device_id
        
        commands_to_send.extend(pending_commands["default"])
        pending_commands["default"] = []  # Clear the default commands
    
    logger.info(f"Device {device_id} checked in, sending {len(commands_to_send)} commands")
    return jsonify({"commands": commands_to_send})

@app.route('/api/check_commands/report', methods=['POST'])
def report_command():
    data = request.json
    if not data or not all(k in data for k in ['device_id', 'command_id', 'success']):
        return jsonify({"error": "Missing required fields"}), 400
    
    device_id = data['device_id']
    command_id = data['command_id']
    success = data['success']
    message = data.get('message', '')
    
    # Update command status
    if command_id in command_status:
        command_status[command_id].update({
            "status": "completed" if success else "failed",
            "message": message,
            "completed_at": datetime.now().isoformat()
        })
    
    logger.info(f"Command {command_id} reported as {'successful' if success else 'failed'}")
    return jsonify({"status": "success"})

@app.route('/api/devices', methods=['GET'])
def get_devices():
    # Convert timestamps to readable format
    readable_devices = {}
    for device_id, timestamp in device_status.items():
        last_seen = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
        readable_devices[device_id] = {
            "last_seen": last_seen,
            "active": (time.time() - timestamp) < 60  # Consider active if seen in last minute
        }
    
    return jsonify({"devices": readable_devices})

@app.route('/api/commands', methods=['GET'])
def get_commands():
    return jsonify({"commands": list(command_status.values())})

@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)