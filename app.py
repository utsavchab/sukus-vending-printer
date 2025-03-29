from flask import Flask, request, render_template, redirect, url_for
import os
import cups
import tempfile
from PyPDF2 import PdfReader, PdfWriter
from io import BytesIO

app = Flask(__name__)

# Configure the uploads folder
UPLOAD_FOLDER = './uploads'
ALLOWED_EXTENSIONS = {'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# CUPS connection
conn = cups.Connection()
printer_name = conn.getDefault()
print(printer_name)

# Function to check allowed file types
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Function to handle PDF processing and page selection
def process_pdf(file_path, selected_pages, num_copies, layout, pages_per_sheet):
    reader = PdfReader(file_path)
    writer = PdfWriter()

    # Handle selected pages
    if selected_pages:
        pages = []
        for page_range in selected_pages.split(','):
            if '-' in page_range:
                start, end = map(int, page_range.split('-'))
                pages.extend(range(start - 1, end))  # Pages are zero-indexed
            else:
                pages.append(int(page_range) - 1)

        for page_num in pages:
            writer.add_page(reader.pages[page_num])
    else:
        # If no pages are selected, print the entire document
        for page_num in range(len(reader.pages)):
            writer.add_page(reader.pages[page_num])

    # Create temporary file to store the new PDF with selected pages and options
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    with open(temp_file.name, 'wb') as f:
        writer.write(f)

    return temp_file.name

# Home route
@app.route('/')
def index():
    return render_template('index.html')

# File upload route
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return "No file part", 400
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400
    if file and allowed_file(file.filename):
        filename = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filename)

        # Collect user preferences
        selected_pages = request.form.get('selected_pages')  # e.g., '1, 2-4'
        num_copies = int(request.form.get('num_copies', 1))  # default 1 copy
        layout = request.form.get('layout', 'portrait')  # 'portrait' or 'landscape'
        pages_per_sheet = int(request.form.get('pages_per_sheet', 1))  # Default 1 page per sheet

        # Process the PDF based on the selected options
        processed_pdf_path = process_pdf(filename, selected_pages, num_copies, layout, pages_per_sheet)

        # Send the processed PDF file to the printer
        printer_name = conn.getDefault()  # Get the default printer
        print(printer_name)
        try:
            for _ in range(num_copies):
                conn.printFile(printer_name, processed_pdf_path, "Print Job", {})
        except Exception as e:
            return f"Error while printing: {str(e)}", 500

        # Clean up temporary file after printing
        os.remove(processed_pdf_path)

        return "File uploaded, processed, and printing started!"
    return "Invalid file format", 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

