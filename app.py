import os
import threading
import webbrowser
import shutil
import io
from flask import Flask, request, render_template, flash, redirect, url_for, send_file
from werkzeug.utils import secure_filename
from mistralai import Mistral
from langchain_community.document_loaders import PyPDFLoader
from dotenv import load_dotenv
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph
from reportlab.lib.units import inch
import logging

# Configura o logging para mostrar informações no console
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

# --- Configuração Inicial ---
# Garante que os caminhos sejam relativos à localização do script
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Nova estrutura de diretórios para organização dos currículos
CURRICULOS_DIR = os.path.join(BASE_DIR, 'curriculos')
ORIGINAL_RENAMED_FOLDER = os.path.join(CURRICULOS_DIR, 'arquivos_originais_renomeados')
ANONYMOUS_FOLDER = os.path.join(CURRICULOS_DIR, 'arquivos_anonimos')
TEMP_ZIP_DIR = os.path.join(BASE_DIR, 'temp_zips') # Diretório temporário para a criação do arquivo ZIP

ALLOWED_EXTENSIONS = {'pdf'}

app = Flask(__name__)
app.config['ORIGINAL_RENAMED_FOLDER'] = ORIGINAL_RENAMED_FOLDER
app.config['ANONYMOUS_FOLDER'] = ANONYMOUS_FOLDER
app.config['TEMP_ZIP_DIR'] = TEMP_ZIP_DIR
# A chave secreta é importante para segurança de sessões Flask (mensagens flash, etc.)
app.config['SECRET_KEY'] = os.getenv("FLASK_SECRET_KEY", "uma-chave-secreta-padrao-para-desenvolvimento")

# Garante que os diretórios necessários existam ao iniciar a aplicação
os.makedirs(ORIGINAL_RENAMED_FOLDER, exist_ok=True)
os.makedirs(ANONYMOUS_FOLDER, exist_ok=True)
os.makedirs(TEMP_ZIP_DIR, exist_ok=True)


# --- Funções Auxiliares ---

def allowed_file(filename):
    """Verifica se a extensão do arquivo é permitida (apenas PDF)."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def clean_curriculos_folders():
    """
    Limpa o conteúdo dos diretórios onde os currículos são armazenados.
    Isso evita o acúmulo de arquivos de sessões anteriores.
    """
    logging.info("Iniciando limpeza das pastas de currículos...")
    for folder in [ORIGINAL_RENAMED_FOLDER, ANONYMOUS_FOLDER]:
        if os.path.exists(folder):
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path) # Remove arquivo ou link simbólico
                        logging.info(f"Removido arquivo: {file_path}")
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path) # Remove subdiretório e seu conteúdo
                        logging.info(f"Removido diretório: {file_path}")
                except Exception as e:
                    logging.error(f'Falha ao remover {file_path}. Razão: {e}')
    logging.info("Pastas de currículos limpas com sucesso.")


def anonymize_resume_text(text: str) -> str:
    """
    Envia o texto do currículo para a API da Mistral para anonimização.
    Substitui informações pessoais por marcadores genéricos.
    """
    
    # É altamente recomendado carregar a chave da API de variáveis de ambiente.
    # O valor padrão é apenas para facilitar o teste inicial se a variável de ambiente não estiver configurada.
    client = Mistral(os.getenv("MISTRAL_API_KEY", "SUA_CHAVE_MISTRAL_AQUI")) 
    
    prompt = f"""
    Com base no seguinte texto de currículo, processe-o para remover informações de identificação pessoal, substituindo-as por marcadores genéricos. O objetivo é criar um currículo "cego" que evite vieses de recrutamento.

    Siga estas diretrizes de anonimização estritamente:

    * **Nome Completo:** Substitua por "**CANDIDATO(A)**".
    * **Telefone:** Substitua por "**TELEFONE REMOVIDO**".
    * **Endereço Físico:** Substitua por "**ENDEREÇO REMOVIDO**".
    * **Endereço de Email:** Substitua por "**EMAIL REMOVIDO**".
    * **Links Pessoais (LinkedIn, GitHub, Portfólio):** Substitua por "**LINK REMOVIDO**".
    * **Nome da Empresa:** Na seção "Experiência Profissional", substitua o nome da empresa por "**EMPRESA CONFIDENCIAL**". Mantenha o cargo, as datas e as responsabilidades.
    * **Nome da Instituição de Ensino:** Na seção "Formação Acadêmica", substitua o nome da instituição por "**INSTITUIÇÃO DE ENSINO**". Mantenha o nome do curso e as datas.

    Mantenha todo o restante do conteúdo, como descrições de cargo, habilidades técnicas, projetos e idiomas, exatamente como está. A estrutura (títulos, listas, parágrafos) deve ser preservada.

    **Texto do Currículo para Processar:**
    ---
    {text}
    ---

    **Formato da Saída:** Retorne apenas o currículo processado em formato de texto simples, com as informações sensíveis substituídas conforme as diretrizes.
    """

    try:
        response = client.chat.complete(
            model="mistral-large-latest", # Modelo recomendado pela Mistral para tasks mais complexas
            temperature=0.0, # Temperatura 0 para resultados mais determinísticos e menos criativos
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"Erro na API da Mistral: {e}")
        return "Erro ao processar o currículo. Verifique a chave da API e a conexão."

def create_anonymous_pdf(text: str, output_filename: str):
    """Cria um arquivo PDF a partir do texto anonimizado."""
    output_path = os.path.join(app.config['ANONYMOUS_FOLDER'], output_filename)
    c = canvas.Canvas(output_path, pagesize=letter)
    width, height = letter

    styles = getSampleStyleSheet()
    style = styles['BodyText']
    style.fontSize = 10
    style.leading = 14  # Espaçamento entre linhas para melhor legibilidade

    # Margens do documento
    margin_x = 0.75 * inch
    margin_y = 0.75 * inch
    text_width = width - 2 * margin_x

    # Transforma quebras de linha em tags <br/> para que ReportLab as interprete corretamente
    formatted_text = text.replace('\n', '<br/>')
    
    p = Paragraph(formatted_text, style)
    
    # Calcula a altura necessária para o texto e desenha no PDF
    p_width, p_height = p.wrapOn(c, text_width, height) 
    
    # Aviso se o texto for muito longo para uma única página
    if p_height > height - 2 * margin_y:
        logging.warning(f"O texto é muito longo para uma única página no PDF: {output_filename}. Parte do conteúdo pode ter sido truncada ou exigir rolagem.")

    p.drawOn(c, margin_x, height - margin_y - p_height)
    
    c.save()
    return output_filename

# --- Rotas da Aplicação Flask ---

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        # Limpa as pastas de currículos no início de CADA nova submissão
        clean_curriculos_folders()

        # Verifica se 'files[]' está no request.files (nome do input no HTML)
        if 'files[]' not in request.files:
            flash('Nenhum arquivo selecionado.', 'error')
            return redirect(request.url)
        
        files = request.files.getlist('files[]') # Obtém a lista de arquivos enviados
        
        # Verifica se a lista de arquivos está vazia ou se todos os nomes de arquivo estão vazios
        if not files or all(file.filename == '' for file in files):
            flash('Nenhum arquivo selecionado para processamento.', 'error')
            return redirect(request.url)
        
        processed_count = 0
        for i, file in enumerate(files):
            if file and allowed_file(file.filename):
                original_filename = secure_filename(file.filename) # Garante um nome de arquivo seguro
                
                # Define os novos nomes de arquivo para o original renomeado e o anonimizado
                candidate_number = i + 1
                new_original_filename = f"Candidato_{candidate_number}_original.pdf"
                new_anonymous_filename = f"Candidato_{candidate_number}_anonimo.pdf"

                original_filepath = os.path.join(app.config['ORIGINAL_RENAMED_FOLDER'], new_original_filename)
                
                try:
                    # Salva o arquivo original no novo local
                    file.save(original_filepath)
                    logging.info(f"Arquivo original salvo em: {original_filepath}")

                    # --- VERIFICAÇÃO CRÍTICA: Garante que o arquivo existe após ser salvo ---
                    if not os.path.exists(original_filepath):
                        error_msg = f"Erro interno: O arquivo '{original_filename}' NÃO foi encontrado em '{original_filepath}' após a tentativa de salvamento. Verifique permissões de pasta."
                        logging.error(error_msg)
                        flash(error_msg, 'error')
                        continue # Pula para o próximo arquivo se este falhou
                    # --- FIM DA VERIFICAÇÃO ---

                    # 1. Extrair texto do PDF usando PyPDFLoader
                    loader = PyPDFLoader(original_filepath)
                    pages = loader.load()
                    resume_text = "\n".join([doc.page_content for doc in pages])
                    logging.info(f"Texto extraído do arquivo: {original_filename}")

                    # 2. Anonimizar o texto usando a API da Mistral
                    anonymous_text = anonymize_resume_text(resume_text)
                    if anonymous_text.startswith("Erro ao processar"):
                        flash(f"Erro ao anonimizar o arquivo '{original_filename}': {anonymous_text}", 'error')
                        continue 

                    # 3. Criar o novo PDF com o texto anonimizado
                    create_anonymous_pdf(anonymous_text, new_anonymous_filename)
                    processed_count += 1
                    logging.info(f"Arquivo '{original_filename}' processado e salvo como '{new_anonymous_filename}'.")

                except Exception as e:
                    logging.error(f"Erro inesperado ao processar o arquivo '{original_filename}': {e}", exc_info=True)
                    flash(f"Erro ao processar o arquivo '{original_filename}'. Detalhes: {str(e)}", 'error')
                    # Garante que o arquivo original seja removido se ocorrer um erro após o salvamento
                    if os.path.exists(original_filepath):
                        os.remove(original_filepath)
            else:
                flash(f"Formato de arquivo inválido para '{file.filename}'. Por favor, envie apenas PDFs.", 'error')

        if processed_count > 0:
            flash(f"{processed_count} currículo(s) processado(s) com sucesso!", 'success')
            # Renderiza a página com o botão de download do ZIP
            return render_template('index.html', show_download_zip=True) 
        else:
            flash("Nenhum currículo foi processado. Verifique os arquivos e tente novamente.", 'error')
        
        return redirect(url_for('upload_file'))

    # Método GET: Apenas mostra a página inicial (sem botão de download do ZIP inicialmente)
    return render_template('index.html', show_download_zip=False)


@app.route('/download_all_curriculos')
def download_all_curriculos():
    """
    Cria um arquivo ZIP do diretório 'curriculos' e o envia para download.
    Limpa o arquivo ZIP temporário após o envio.
    """
    zip_filename_base = "curriculos_anonimizados"
    # O make_archive adiciona a extensão .zip automaticamente
    zip_path_without_ext = os.path.join(app.config['TEMP_ZIP_DIR'], zip_filename_base)
    
    try:
        # Cria um arquivo zip do conteúdo da pasta 'curriculos'
        # root_dir=BASE_DIR significa que o caminho base para o zip será o diretório do seu script
        # base_dir='curriculos' significa que o conteúdo da pasta 'curriculos' será zipado,
        # e as subpastas (arquivos_originais_renomeados, arquivos_anonimos) estarão diretamente dentro do ZIP.
        archive_path = shutil.make_archive(zip_path_without_ext, 'zip', root_dir=BASE_DIR, base_dir='curriculos')
        logging.info(f"Arquivo ZIP criado temporariamente em: {archive_path}")
        
        # Lê o arquivo zip para a memória para envio
        with open(archive_path, 'rb') as f:
            zip_data = io.BytesIO(f.read())
        
        # Deleta o arquivo zip temporário do disco após ser lido para a memória
        os.remove(archive_path)
        logging.info(f"Arquivo ZIP temporário '{archive_path}' foi removido do disco.")

        zip_data.seek(0) # Volta o objeto BytesIO para o início antes de enviar

        return send_file(zip_data, 
                         mimetype='application/zip', 
                         as_attachment=True, 
                         download_name='curriculos_anonimizados.zip')

    except Exception as e:
        logging.error(f"Erro ao criar ou enviar o arquivo ZIP: {e}", exc_info=True)
        flash(f"Ocorreu um erro ao gerar o arquivo ZIP: {str(e)}", 'error')
        return redirect(url_for('upload_file'))


if __name__ == '__main__':
    port = 5000
    host = "127.0.0.1"
    url = f"http://{host}:{port}"
    
    # Inicia um thread para abrir o navegador automaticamente após um pequeno atraso
    threading.Timer(1.25, lambda: webbrowser.open(url)).start()
    
    # Inicia a aplicação Flask em modo de depuração (debug=True é bom para desenvolvimento)
    app.run(host=host, port=port, debug=True)   