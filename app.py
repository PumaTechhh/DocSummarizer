from flask import Flask, request, render_template, jsonify
import os
import docx2txt
import requests
import pdfplumber
import sys
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)

# Increase recursion limit as a fallback (though we use iteration)
sys.setrecursionlimit(2000)

app = Flask(__name__)

# Set maximum file size to 16 MB
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# API Configuration
API_URL = "https://api-inference.huggingface.co/models/mistralai/Mixtral-8x7B-Instruct-v0.1"
HF_TOKEN = "Your Hugging Face Token Here"
headers = {"Authorization": f"Bearer {HF_TOKEN}"}

# Updated prompt to enforce detailed, structured summaries with a proper ending
DEFAULT_PROMPT = (
    "Create a comprehensive, complete, and detailed summary that includes: "
    "1) Main concepts and key points, including specific mechanisms and comparisons, "
    "2) Important supporting details, such as roles of different cell types and processes like autophagy, "
    "3) Clear conclusion and implications, highlighting future research needs. "
    "Use original phrasing, maintain logical flow, and ensure the summary is self-contained and well-structured. "
    "End the summary with a conclusive statement that ties together the main points, emphasizes the topic's importance, "
    "and provides a forward-looking perspective on the implications or future advancements."
)

def query_huggingface_api(payload: dict) -> dict:
    try:
        response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"API Error: {str(e)}")
        return {"error": f"API Error: {str(e)}"}

def estimate_tokens(text):
    return len(text) // 4  # Rough estimate: 4 characters per token

def split_text(text, chunk_size=30000, overlap=6000):  # 20% overlap
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        start += chunk_size - overlap  # Move forward with overlap
    return chunks

def read_file(file_path: str) -> str:
    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError("File not found")
        
        if file_path.lower().endswith('.docx'):
            text = docx2txt.process(file_path)
        elif file_path.lower().endswith('.txt'):
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
        elif file_path.lower().endswith('.pdf'):
            with pdfplumber.open(file_path) as pdf:
                text = ""
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
        else:
            raise ValueError("Unsupported file format")
        
        if not text.strip():
            raise ValueError("File appears to be empty")
        
        return text.strip()
    except Exception as e:
        logging.error(f"File processing error: {str(e)}")
        raise RuntimeError(f"File processing error: {str(e)}")

def summarize_chunk(text):
    if not text.strip():
        return "Error: No text to summarize"
    
    prompt = f"""<s>[INST] <<SYS>>
    {DEFAULT_PROMPT}
    
    Text: {text}
    
    Provide your structured summary after 'Summary:'
    <</SYS>>[/INST]"""
    
    payload = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": 1500,  # Increased to 1500
            "temperature": 0.5,
            "do_sample": True,
            "return_full_text": False
        }
    }
    
    result = query_huggingface_api(payload)
    
    if isinstance(result, dict) and "error" in result:
        return f"API Error: {result['error']}"
    
    if isinstance(result, list) and result:
        generated_text = result[0].get('generated_text', '')
        logging.debug(f"Generated text: {generated_text}")
        if generated_text:
            # Extract summary after marker
            markers = ["Summary:", "summary:", "Conclusion:"]
            summary_text = generated_text
            for marker in markers:
                if marker in generated_text:
                    summary_text = generated_text.split(marker, 1)[1].strip()
                    break
            
            # Ensure proper ending
            if summary_text and summary_text[-1] not in {'.', '!', '?'}:
                summary_text += '.'
            
            # Check if the last sentence is complete
            sentences = summary_text.split('. ')
            if sentences:
                last_sentence = sentences[-1].strip()
                incomplete_indicators = [' and', ' or', ' for', ' to', ' in', ' with', ' on', ' at', ' by', ' as', ' but', ' however', ' although']
                is_incomplete = any(last_sentence.endswith(indicator) for indicator in incomplete_indicators) or not last_sentence.endswith(('.', '!', '?'))
                if is_incomplete:
                    # Remove the incomplete sentence
                    sentences = sentences[:-1]
                    summary_text = '. '.join(sentences).strip()
                    if summary_text and not summary_text.endswith('.'):
                        summary_text += '.'
                    # Append a generic conclusion
                    summary_text += " In conclusion, further research is needed to address the challenges and improve outcomes in this field."
            
            # Check if the summary includes a conclusion
            conclusion_keywords = ["future", "conclusion", "ultimately", "in summary", "therefore", "thus", "hence"]
            has_conclusion = any(keyword in summary_text.lower() for keyword in conclusion_keywords)
            if not has_conclusion:
                summary_text += " In conclusion, further research is needed to address the challenges and improve outcomes in this field."
            
            return summary_text
    
    return "Error: No summary generated"

def split_summary_into_parts(summary, max_part_length=1000):
    if len(summary) <= max_part_length:
        return summary
    
    # Split into parts based on sentences
    sentences = summary.split('. ')
    parts = []
    current_part = []
    current_length = 0
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        sentence_length = len(sentence) + 2  # Account for ". " after rejoining
        if current_length + sentence_length > max_part_length and current_part:
            parts.append('. '.join(current_part))  # Updated: Removed + '.'
            current_part = [sentence]
            current_length = sentence_length
        else:
            current_part.append(sentence)
            current_length += sentence_length
    
    if current_part:
        parts.append('. '.join(current_part))  # Updated: Removed + '.'
    
    # Format parts with headings
    formatted_summary = ""
    for i, part in enumerate(parts, 1):
        if i == 1:
            formatted_summary += f"Part {i}: Overview\n{part}\n\n"
        elif i == len(parts):
            # Ensure the conclusion is complete
            conclusion = part
            conclusion_sentences = conclusion.split('. ')
            if conclusion_sentences:
                last_sentence = conclusion_sentences[-1].strip()
                incomplete_indicators = [' and', ' or', ' for', ' to', ' in', ' with', ' on', ' at', ' by', ' as', ' but', ' however', ' although']
                is_incomplete = any(last_sentence.endswith(indicator) for indicator in incomplete_indicators) or not last_sentence.endswith(('.', '!', '?'))
                if is_incomplete:
                    # Remove the incomplete sentence
                    conclusion_sentences = conclusion_sentences[:-1]
                    conclusion = '. '.join(conclusion_sentences).strip()
                    if conclusion and not conclusion.endswith('.'):
                        conclusion += '.'
            
            # Polish conclusion if too short or incomplete
            if len(conclusion.split()) < 15 or not conclusion.endswith('.'):
                conclusion += " Ultimately, these insights will drive the development of innovative solutions to address the challenges posed by this topic."
            formatted_summary += f"Part {i}: Conclusion\n{conclusion}"
        else:
            formatted_summary += f"Part {i}: Key Details\n{part}\n\n"
    
    return formatted_summary

def generate_summary(text):
    def is_too_long(text):
        return estimate_tokens(text) > 4096
    
    requires_chunking = is_too_long(text)
    
    if not requires_chunking:
        summary = summarize_chunk(text)
        logging.debug(f"Summary for short document: {summary}")
        return summary
    else:
        chunks = split_text(text, chunk_size=30000, overlap=6000)
        chunk_summaries = [summarize_chunk(chunk) for chunk in chunks]
        combined_summaries = ' '.join(chunk_summaries)
        
        if is_too_long(combined_summaries):
            sub_chunks = split_text(combined_summaries, chunk_size=30000, overlap=6000)
            sub_summaries = [summarize_chunk(sub_chunk) for sub_chunk in sub_chunks]
            summary = ' '.join(sub_summaries)
        else:
            summary = summarize_chunk(combined_summaries)
        
        formatted_summary = split_summary_into_parts(summary)
        logging.debug(f"Formatted summary for long document: {formatted_summary}")
        return formatted_summary

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    file_path = os.path.join("uploads", file.filename)
    os.makedirs("uploads", exist_ok=True)
    file.save(file_path)
    
    return jsonify({'filename': file.filename})

@app.route('/summarize', methods=['POST'])
def summarize():
    data = request.get_json()
    filename = data.get('filename')
    
    if not filename:
        return jsonify({'error': 'No filename provided'}), 400
    
    file_path = os.path.join("uploads", filename)
    
    try:
        text = read_file(file_path)
        summary = generate_summary(text)
        os.remove(file_path)
        return jsonify({'summary': summary})
    except Exception as e:
        logging.error(f"Error generating summary: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
