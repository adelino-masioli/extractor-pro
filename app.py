# app.py - VERSÃO CORRIGIDA PARA FLASK-BABEL 4.0.0+

import os
import io
import csv
import json
import fitz  # PyMuPDF
import requests
from bs4 import BeautifulSoup
from flask import (
    Flask, request, render_template, make_response, send_file, session, redirect, url_for, g
)
# CORREÇÃO 1: Remover 'localeselector' da importação direta
from flask_babel import Babel, _, lazy_gettext as _l
from werkzeug.utils import secure_filename
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

# --- Configuração do App e Babel ---
app = Flask(__name__)

# Chave secreta (ESSENCIAL!)
app.secret_key = os.environ.get('FLASK_SECRET_KEY')
if not app.secret_key:
    print("\n*** ERRO CRÍTICO: FLASK_SECRET_KEY não definida! ***\n")
    app.secret_key = 'temp-unsafe-key-for-initial-run-only-define-env-var'
    print("*** ATENÇÃO: Usando chave secreta temporária e insegura. Defina FLASK_SECRET_KEY! ***")

# Configuração do Babel
app.config['LANGUAGES'] = {
    'pt': 'Português',
    'en': 'English',
    'es': 'Español'
}
app.config['BABEL_DEFAULT_LOCALE'] = 'pt'

# --- Fim Configuração ---

# --- Seleção de Idioma ---

# Função seletora de idioma (SEM decorador)
def get_locale():
    # 1. Prioridade: Usuário escolheu idioma na sessão
    if 'language' in session:
        if session['language'] in app.config['LANGUAGES'].keys():
            return session['language']
    # 2. Tenta detectar pelo cabeçalho Accept-Language do navegador
    best_match = request.accept_languages.best_match(app.config['LANGUAGES'].keys())
    # 3. Fallback para o idioma padrão
    return best_match if best_match else app.config['BABEL_DEFAULT_LOCALE']

# CORREÇÃO 3: Passar a função 'get_locale' na inicialização do Babel
babel = Babel(app, locale_selector=get_locale)

# CORREÇÃO 2: Certifique-se que a linha @babel.localeselector ou @localeselector FOI REMOVIDA de cima da função get_locale()


# Rota para o usuário definir o idioma
@app.route('/language/<lang>')
def set_language(lang=None):
    if lang in app.config['LANGUAGES'].keys():
        session['language'] = lang
        # print(f"DEBUG: Idioma definido na sessão para: {lang}") # Descomente para debug
    return redirect(request.referrer or url_for('index'))

# Disponibiliza o idioma atual para os templates
@app.context_processor
def inject_conf_var():
    lang = session.get('language')
    if lang not in app.config['LANGUAGES']:
        lang = get_locale()
    return dict(CURRENT_LANG=lang)
# --- Fim Seleção Idioma ---


app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
ALLOWED_EXTENSIONS = {'pdf'}
REQUESTS_TIMEOUT = 20

def allowed_file(filename):
    """Verifica se a extensão do arquivo é permitida."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_valid_url(url):
    """Validação básica de formato de URL."""
    try:
        result = urlparse(url)
        return all([result.scheme in ['http', 'https'], result.netloc])
    except ValueError:
        return False

# --- Funções de Extração (com mensagens traduzíveis) ---
def extract_pdf_text(byte_stream, source_description="PDF"):
    """Extrai texto de um stream de bytes de um PDF."""
    text = ""
    doc = None
    try:
        doc = fitz.open(stream=byte_stream, filetype="pdf")
        if not doc.is_pdf:
             raise ValueError(_('The content from "%(source)s" does not appear to be a valid PDF.', source=source_description))
        if doc.is_encrypted:
             if not doc.authenticate(""):
                  raise ValueError(_('The PDF from "%(source)s" is password protected and cannot be processed.', source=source_description))
             # print(f"Aviso: PDF '{source_description}' estava bloqueado, mas acessível sem senha.")

        for page_num in range(len(doc)):
            try:
                page = doc.load_page(page_num)
                text += page.get_text("text")
            except Exception as page_error:
                print(f"Aviso: Falha ao processar página {page_num + 1} de '{source_description}': {page_error}")
                # text += _('\n[Error processing page %(num)s]\n', num=page_num + 1)
        if not text.strip():
             raise ValueError(_('No extractable text found in the PDF "%(source)s". It might be an image file, empty, or contain only graphics.', source=source_description))
        return text
    except fitz.fitz.FileDataError as e:
        raise ValueError(_('Invalid or corrupted PDF file from "%(source)s": %(error)s', source=source_description, error=e))
    except ValueError as ve:
        raise ve
    except Exception as e:
        print(f"Erro inesperado ao ler PDF de {source_description}: {type(e).__name__} - {e}")
        if "cannot open" in str(e) or "syntax error" in str(e):
             raise ValueError(_('Invalid or unsupported PDF format from "%(source)s": %(error)s', source=source_description, error=e))
        else:
             raise ValueError(_('Unexpected error processing the PDF from "%(source)s": %(error)s', source=source_description, error=e))
    finally:
         if doc: doc.close()

def extract_html_text(html_content, source_url="Página Web"):
    """Extrai texto visível de conteúdo HTML."""
    try:
        soup = BeautifulSoup(html_content, 'lxml')
        for element in soup(["script", "style", "head", "title", "meta", "[document]", "header", "footer", "nav", "aside", "form", "button", "select", "textarea", "img", "svg", "iframe"]):
            element.decompose()
        text = soup.get_text(separator=' ', strip=True)
        if not text:
            raise ValueError(_('No main text found at "%(url)s". The page might be empty, heavily reliant on JavaScript, or the main content was removed during parsing.', url=source_url))
        return text
    except Exception as e:
        print(f"Erro ao processar HTML de {source_url}: {type(e).__name__} - {e}")
        raise ValueError(_('Error parsing HTML content from URL "%(url)s": %(error)s', url=source_url, error=e))
# --- Fim Funções de Extração ---


# --- Rotas Principais ---
@app.route('/')
def index():
    """Renderiza a página inicial."""
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process_input():
    """Processa upload de PDF OU URL (PDF ou HTML)."""
    pdf_file = request.files.get('pdf_file')
    input_url = request.form.get('input_url', '').strip()
    source_description = _("input")
    byte_stream = None
    filename_base = "extracted_data"
    extracted_text = None

    try:
        # --- Upload ---
        if pdf_file and pdf_file.filename != '':
            if not allowed_file(pdf_file.filename):
                raise ValueError(_('Invalid file type for upload.') + f" ({pdf_file.filename}). " + _('Please upload a PDF.'))
            filename = secure_filename(pdf_file.filename)
            source_description = filename
            filename_base = filename.rsplit('.', 1)[0] if '.' in filename else filename
            byte_stream = io.BytesIO()
            pdf_file.save(byte_stream)
            byte_stream.seek(0)
            extracted_text = extract_pdf_text(byte_stream, source_description)
        # --- URL ---
        elif input_url:
            if not is_valid_url(input_url):
                raise ValueError(_('Invalid URL format.'))
            source_description = input_url
            try:
                 parsed_url = urlparse(input_url)
                 url_filename = os.path.basename(parsed_url.path); fn = url_filename
                 if fn and '.' in fn: filename_base = fn.rsplit('.', 1)[0]
                 elif fn: filename_base = fn
                 else: filename_base = parsed_url.netloc.replace('.', '_') or "extracted_from_url"
            except Exception: filename_base = "extracted_from_url"

            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
                response = requests.get(input_url, timeout=REQUESTS_TIMEOUT, headers=headers, allow_redirects=True)
                response.raise_for_status()
                content_type = response.headers.get('content-type', '').lower()
                content_length = int(response.headers.get('content-length', 0))

                if content_length > app.config['MAX_CONTENT_LENGTH']:
                     raise ValueError(_('The content at the URL is too large (larger than %(max_size)sMB).', max_size=app.config['MAX_CONTENT_LENGTH']//(1024*1024)))

                if 'application/pdf' in content_type:
                    byte_stream_url = io.BytesIO(response.content)
                    extracted_text = extract_pdf_text(byte_stream_url, source_description)
                    byte_stream_url.close()
                elif 'text/html' in content_type or 'application/xhtml+xml' in content_type:
                    extracted_text = extract_html_text(response.content, source_description)
                    # extracted_text += "\n\n" + _('[Note: Content loaded by JavaScript might not be included.]')
                else:
                    raise ValueError(_('The URL does not point to a supported PDF or HTML page (Content-Type: %(ctype)s).', ctype=content_type))
            except requests.exceptions.Timeout:
                raise ValueError(_('Timeout (%(sec)ss) when trying to access the URL: %(url)s', sec=REQUESTS_TIMEOUT, url=input_url))
            except requests.exceptions.RequestException as e:
                 error_msg = str(e).split('\n')[0]
                 raise ValueError(_('Network error accessing the URL %(url)s: %(error)s', url=input_url, error=error_msg))

        # --- Nenhum Input ---
        else:
            raise ValueError(_('No PDF file was uploaded or valid URL provided.'))

        # --- Resultado ---
        if extracted_text:
            session['extracted_text'] = extracted_text
            session['original_filename'] = filename_base
            return render_template('results.html', text=extracted_text, source=source_description)
        else:
             raise ValueError(_('Could not extract text from "%(source)s" (empty result after processing).', source=source_description))

    except ValueError as e:
         return render_template('results.html', error=str(e))
    except Exception as e:
        print(f"DEBUG: Erro inesperado no processamento: {type(e).__name__} - {e}")
        import traceback
        traceback.print_exc()
        return render_template('results.html', error=_('An unexpected error occurred during processing. Check server logs for details.'))
    finally:
         if pdf_file and byte_stream and not byte_stream.closed: byte_stream.close()


@app.route('/download/<format>')
def download_file(format):
    """Fornece o texto extraído para download no formato solicitado."""
    extracted_text = session.get('extracted_text')
    base_filename = session.get('original_filename', 'extracted_data')

    if not extracted_text:
        return _('Error: No extracted text found in the current session for download. Please try processing a PDF or URL again.'), 404

    allowed_formats = {'txt', 'csv', 'json'}
    if format not in allowed_formats: return _('Invalid download format.'), 400
    download_filename = f"{base_filename}.{format}"

    buffer = None
    mimetype = 'application/octet-stream'
    try:
        if format == 'txt':
            buffer = io.BytesIO(extracted_text.encode('utf-8'))
            mimetype = 'text/plain; charset=utf-8'
        elif format == 'csv':
            string_buffer = io.StringIO()
            writer = csv.writer(string_buffer)
            writer.writerow([_('Extracted Text')]) # Cabeçalho traduzido
            lines = extracted_text.splitlines()
            for line in lines: writer.writerow([line])
            string_buffer.seek(0)
            buffer = io.BytesIO(string_buffer.getvalue().encode('utf-8'))
            string_buffer.close()
            mimetype = 'text/csv; charset=utf-8'
        elif format == 'json':
            lines = extracted_text.splitlines()
            data = {"lines": lines}
            json_str = json.dumps(data, ensure_ascii=False, indent=2)
            buffer = io.BytesIO(json_str.encode('utf-8'))
            mimetype = 'application/json; charset=utf-8'

        return send_file(buffer, mimetype=mimetype, as_attachment=True, download_name=download_filename)
    except Exception as e:
        print(f"DEBUG DOWNLOAD: Erro ao gerar/enviar arquivo {format}: {type(e).__name__} - {e}")
        if buffer and hasattr(buffer, 'closed') and not buffer.closed: buffer.close()
        return _('Error generating the file for download.'), 500
# --- Fim Rotas ---

# Roda o servidor
if __name__ == '__main__':
    # Lembre-se de definir debug=False para produção e usar um servidor WSGI
    app.run(debug=True, host='0.0.0.0', port=5000)