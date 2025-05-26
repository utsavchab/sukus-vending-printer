import requests
import time
import json
import os
import cups
import tempfile
from PyPDF2 import PdfReader, PdfWriter
import base64
from io import BytesIO
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("printer_client.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("PrinterClient")

# Configuration
DEVICE_ID = "pi_printer_001"  # Unique ID for this Raspberry Pi
SERVER_URL = "https://sukus-vending-printer.onrender.com/api/check_commands"  # Replace with your server URL
POLL_INTERVAL = 10  # Seconds between polls
UPLOAD_FOLDER = './downloads'  # Folder to store downloaded PDFs
RETRY_INTERVAL = 10  # Seconds to wait after a connection error

# Ensure download directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# CUPS connection
try:
    conn = cups.Connection()
    printer_name = conn.getDefault()
    logger.info(f"Default printer: {printer_name}")
    if not printer_name:
        available_printers = conn.getPrinters()
        if available_printers:
            printer_name = list(available_printers.keys())[0]
            logger.info(f"No default printer found, using: {printer_name}")
        else:
            logger.error("No printers found")
            raise Exception("No printers available")
except Exception as e:
    logger.error(f"Failed to connect to CUPS: {str(e)}")
    raise

# Function to handle PDF processing and page selection
def process_pdf(file_path, print_options):
    logger.info(f"Processing PDF: {file_path}")
    reader = PdfReader(file_path)
    writer = PdfWriter()
    
    # Handle selected pages
    selected_pages = print_options.get('selected_pages', '')
    if selected_pages:
        pages = []
        for page_range in selected_pages.split(','):
            if '-' in page_range:
                start, end = map(int, page_range.split('-'))
                pages.extend(range(start - 1, end))  # Pages are zero-indexed
            else:
                pages.append(int(page_range) - 1)

        for page_num in pages:
            if 0 <= page_num < len(reader.pages):
                writer.add_page(reader.pages[page_num])
    else:
        # If no pages are selected, print the entire document
        for page_num in range(len(reader.pages)):
            writer.add_page(reader.pages[page_num])

    # Create temporary file to store the new PDF with selected pages
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    with open(temp_file.name, 'wb') as f:
        writer.write(f)

    return temp_file.name

# Function to send a PDF to the printer
def print_pdf(pdf_path, print_options):
    logger.info(f"Sending PDF to printer: {pdf_path}")
    
    # Get print options
    num_copies = int(print_options.get('num_copies', 1))
    layout = print_options.get('layout', 'portrait')
    pages_per_sheet = int(print_options.get('pages_per_sheet', 1))
    
    # Set up CUPS printing options
    cups_options = {}
    if layout == 'landscape':
        cups_options['orientation-requested'] = '4'  # Landscape
    else:
        cups_options['orientation-requested'] = '3'  # Portrait
    
    if pages_per_sheet > 1:
        cups_options['number-up'] = str(pages_per_sheet)
    
    try:
        job_id = conn.printFile(printer_name, pdf_path, "Print Job", cups_options)
        logger.info(f"Print job submitted with ID: {job_id}")
        return True, f"Print job submitted with ID: {job_id}"
    except Exception as e:
        error_msg = f"Error while printing: {str(e)}"
        logger.error(error_msg)
        return False, error_msg

# Function to handle a print command
def handle_print_command(command):
    logger.info(f"Processing print command: {command.get('command_id', 'unknown')}")
    
    try:
        # Extract PDF data and options from command
        pdf_data_base64 = command.get('pdf_data')
        print_options = command.get('print_options', {})
        command_id = command.get('command_id')
        
        if not pdf_data_base64:
            return False, "No PDF data in command"
        
        # Decode PDF data
        pdf_data = base64.b64decode(pdf_data_base64)
        
        # Save PDF to a temporary file
        filename = f"print_job_{command_id}_{int(time.time())}.pdf"
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        
        with open(file_path, 'wb') as f:
            f.write(pdf_data)
        
        # Process the PDF based on the selected options
        processed_pdf_path = process_pdf(file_path, print_options)
        
        # Send to printer
        success, message = print_pdf(processed_pdf_path, print_options)
        
        # Clean up temporary files
        try:
            os.remove(processed_pdf_path)
            os.remove(file_path)
        except Exception as e:
            logger.warning(f"Failed to clean up temporary files: {str(e)}")
        
        return success, message
    
    except Exception as e:
        error_msg = f"Error processing print command: {str(e)}"
        logger.error(error_msg)
        return False, error_msg

# Function to report command results back to server
def report_command_result(command_id, success, message):
    logger.info(f"Reporting result for command {command_id}: {'Success' if success else 'Failed'}")
    
    try:
        payload = {
            'device_id': DEVICE_ID,
            'command_id': command_id,
            'success': success,
            'message': message
        }
        
        response = requests.post(
            f"{SERVER_URL}/report",
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            logger.info(f"Successfully reported result for command {command_id}")
            return True
        else:
            logger.warning(f"Failed to report result for command {command_id}: {response.status_code}")
            return False
    
    except Exception as e:
        logger.error(f"Error reporting command result: {str(e)}")
        return False

# Main polling loop
def main_loop():
    logger.info(f"Starting polling client with device ID: {DEVICE_ID}")
    
    while True:
        try:
            # Poll the server for commands
            logger.info("Polling server for commands...")
            
            response = requests.post(
                SERVER_URL,
                json={'device_id': DEVICE_ID},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if 'commands' in data and data['commands']:
                    for command in data['commands']:
                        command_id = command.get('command_id', 'unknown')
                        logger.info(f"Received command: {command_id}")
                        
                        if command.get('type') == 'print':
                            success, message = handle_print_command(command)
                            report_command_result(command_id, success, message)
                        else:
                            logger.warning(f"Unknown command type: {command.get('type')}")
                            report_command_result(command_id, False, "Unknown command type")
                else:
                    logger.info("No commands received")
            
            else:
                logger.warning(f"Server returned non-200 status code: {response.status_code}")
            
            time.sleep(POLL_INTERVAL)
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Connection error: {str(e)}")
            logger.info(f"Retrying in {RETRY_INTERVAL} seconds...")
            time.sleep(RETRY_INTERVAL)
        
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            logger.info(f"Continuing in {RETRY_INTERVAL} seconds...")
            time.sleep(RETRY_INTERVAL)

if __name__ == '__main__':
    try:
        main_loop()
    except KeyboardInterrupt:
        logger.info("Stopping client due to keyboard interrupt")
    except Exception as e:
        logger.critical(f"Fatal error: {str(e)}")