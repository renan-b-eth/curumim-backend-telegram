import os
from dotenv import load_dotenv
import uuid
import boto3
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Configurar Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Carregar variáveis de ambiente ---
load_dotenv()

# --- Credenciais Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    logger.error("TOKEN do bot do Telegram não encontrado. Defina TELEGRAM_BOT_TOKEN no seu .env")
    exit(1)

# --- Credenciais Cloudflare R2 ---
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")

R2_ENDPOINT_URL_PRIVATE = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else None
R2_ENDPOINT_URL_PUBLIC = f"https://pub-{R2_ACCOUNT_ID}.r2.dev" if R2_ACCOUNT_ID else None

s3_client = None
if all([R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT_URL_PRIVATE]):
    try:
        s3_client = boto3.client(
            's3',
            endpoint_url=R2_ENDPOINT_URL_PRIVATE,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name='auto'
        )
        logger.info("Conectado ao Cloudflare R2 para armazenamento de áudios.")
    except Exception as e:
        logger.error(f"Erro ao conectar ao R2: {e}")
        s3_client = None
else:
    logger.warning("Credenciais do R2 incompletas. O upload de áudios estará desativado.")


# --- Gerenciamento de Estado do Chatbot ---
# chat_id: {"stage": "initial", "metadata": {}, "tasks_queue": []}
user_states = {} 

# --- Funções Auxiliares ---
def upload_audio_to_r2(file_path: str, bucket_key: str, content_type: str) -> str | None:
    """Faz o upload de um arquivo de áudio para o Cloudflare R2."""
    if not s3_client:
        logger.error("S3 client (R2) não está configurado. Não é possível fazer upload de áudio.")
        return None
    try:
        s3_client.upload_file(file_path, R2_BUCKET_NAME, bucket_key, ExtraArgs={'ContentType': content_type})
        public_url = f"{R2_ENDPOINT_URL_PUBLIC}/{R2_BUCKET_NAME}/{bucket_key}"
        logger.info(f"Áudio '{bucket_key}' carregado para R2. URL pública: {public_url}")
        return public_url
    except Exception as e:
        logger.error(f"Erro ao fazer upload do arquivo '{file_path}' para R2 como '{bucket_key}': {e}")
        return None

# --- Handlers do Telegram ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envia uma mensagem de boas-vindas e inicia o fluxo de consentimento."""
    chat_id = update.message.chat_id
    sender_name = update.message.from_user.first_name
    
    user_states[chat_id] = {
        "stage": "awaiting_consent", 
        "metadata": {"user_id": chat_id, "telegram_username": sender_name},
        "tasks_queue": []
    }
    logger.info(f"[{chat_id}] Comando /start recebido de {sender_name}. Iniciando consentimento.")

    await update.message.reply_text(
        f"Olá, {sender_name}! Eu sou Curumim, seu assistente de IA para o projeto Angelia. Sua voz pode nos ajudar a desenvolver novas formas de monitorar a saúde. "
        "As gravações serão usadas anonimamente e exclusivamente para pesquisa científica. "
        "Você gostaria de participar e contribuir com sua voz? (Sim/Não)"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envia uma mensagem de ajuda."""
    await update.message.reply_text(
        "Para iniciar ou reiniciar a coleta de dados, digite /start. "
        "Sua voz é valiosa para a pesquisa de saúde!"
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processa todas as mensagens de texto e áudio."""
    chat_id = update.message.chat_id
    user_text = update.message.text
    user_audio = update.message.voice # Objeto de áudio do Telegram
    
    logger.info(f"[{chat_id}] Mensagem recebida: Texto='{user_text}', Áudio='{bool(user_audio)}'")

    # --- Obter/Inicializar Estado do Usuário ---
    if chat_id not in user_states:
        # Se o usuário não iniciou com /start, o bot o direciona.
        await start_command(update, context) 
        return
    
    user_state = user_states[chat_id]
    current_stage = user_state["stage"]
    logger.info(f"[{chat_id}] Estado atual antes da lógica: {current_stage} | Metadata: {user_state['metadata']}")

    # --- Lógica do Chatbot Baseada no Estado ---

    # ESTÁGIO 1: Awaiting Consent
    if current_stage == "awaiting_consent":
        if user_text and user_text.lower() in ["sim", "s"]:
            await update.message.reply_text("Ótimo! Sua participação é muito importante. Para começar, qual o seu *nome* ou um *apelido* que gostaria de usar para esta pesquisa?", parse_mode='Markdown')
            user_state["stage"] = "awaiting_name"
            logger.info(f"[{chat_id}] Consentimento aceito. Transicionou para 'awaiting_name'.")
        elif user_text and user_text.lower() in ["não", "n", "nao"]:
            await update.message.reply_text("Entendi. Agradecemos seu interesse. Se mudar de ideia, pode digitar /start a qualquer momento.")
            user_state["stage"] = "finished" # Finaliza a conversa
            logger.info(f"[{chat_id}] Consentimento recusado. Finalizou.")
        else:
            await update.message.reply_text("Por favor, responda 'Sim' ou 'Não' para indicar seu consentimento.")
            logger.info(f"[{chat_id}] Resposta inválida para consentimento: '{user_text}'.")

    # ESTÁGIO 2: Awaiting Name
    elif current_stage == "awaiting_name":
        if user_text:
            user_state["metadata"]["name"] = user_text.strip()
            await update.message.reply_text(f"Obrigado, {user_state['metadata']['name']}! Agora, qual a sua *idade* (apenas números)?", parse_mode='Markdown')
            user_state["stage"] = "awaiting_age"
            logger.info(f"[{chat_id}] Nome '{user_text}' registrado. Transicionou para 'awaiting_age'.")
        else:
            await update.message.reply_text("Por favor, me diga seu nome ou apelido.")

    # ESTÁGIO 3: Awaiting Age
    elif current_stage == "awaiting_age":
        if user_text and user_text.isdigit() and 5 <= int(user_text) <= 120: # Idade razoável
            user_state["metadata"]["age"] = int(user_text)
            await update.message.reply_text("Idade registrada! Você se considera *Fumante*, *Ex-fumante* ou *Não fumante*?", parse_mode='Markdown')
            user_state["stage"] = "awaiting_smoking_status"
            logger.info(f"[{chat_id}] Idade '{user_text}' registrada. Transicionou para 'awaiting_smoking_status'.")
        else:
            await update.message.reply_text("Por favor, digite sua idade em números (entre 5 e 120 anos).")

    # ESTÁGIO 4: Awaiting Smoking Status
    elif current_stage == "awaiting_smoking_status":
        if user_text and user_text.lower() in ["fumante", "ex-fumante", "não fumante", "nao fumante"]:
            user_state["metadata"]["smoking_status"] = user_text.strip().lower()
            await update.message.reply_text(
                "Obrigado. Você tem algum *diagnóstico de saúde* relevante (ex: Parkinson, Diabetes, Hipertensão, Saudável)?", parse_mode='Markdown')
            user_state["stage"] = "awaiting_diagnosis"
            logger.info(f"[{chat_id}] Status de fumante '{user_text}' registrado. Transicionou para 'awaiting_diagnosis'.")
        else:
            await update.message.reply_text("Por favor, responda com 'Fumante', 'Ex-fumante' ou 'Não fumante'.")

    # ESTÁGIO 5: Awaiting Diagnosis
    elif current_stage == "awaiting_diagnosis":
        if user_text:
            user_state["metadata"]["diagnosis"] = user_text.strip()
            await update.message.reply_text(
                "Entendido. Em uma escala de 1 a 5 (onde 1 é muito calmo e 5 é muito estressado), como você se sente *emocionalmente* agora?", parse_mode='Markdown')
            user_state["stage"] = "awaiting_emotional_state"
            logger.info(f"[{chat_id}] Diagnóstico '{user_text}' registrado. Transicionou para 'awaiting_emotional_state'.")
        else:
            await update.message.reply_text("Por favor, digite seu diagnóstico de saúde (ou 'Saudável' se não tiver).")

    # ESTÁGIO 6: Awaiting Emotional State
    elif current_stage == "awaiting_emotional_state":
        if user_text and user_text.isdigit() and 1 <= int(user_text) <= 5:
            user_state["metadata"]["emotional_state"] = int(user_text)
            await update.message.reply_text(
                "Quase lá! Por favor, descreva o *ambiente* onde você está gravando agora: (Ex: Silencioso, Pouco ruído, Barulhento)", parse_mode='Markdown')
            user_state["stage"] = "awaiting_environment"
            logger.info(f"[{chat_id}] Estado emocional '{user_text}' registrado. Transicionou para 'awaiting_environment'.")
        else:
            await update.message.reply_text("Por favor, use um número de 1 a 5 para descrever seu estado emocional.")

    # ESTÁGIO 7: Awaiting Environment
    elif current_stage == "awaiting_environment":
        if user_text:
            user_state["metadata"]["environment"] = user_text.strip()
            # Iniciar fila de tarefas de áudio
            user_state["tasks_queue"] = ["vogal_a", "vogal_i", "vogal_o", "contagem_1_10"]
            await update.message.reply_text(
                "Perfeito! Seus dados iniciais foram registrados. Agora, vamos para a parte mais importante: a sua voz. "
                "Por favor, encontre um local o mais silencioso possível. "
                "Quando estiver pronto, vamos começar."
            )
            await request_next_audio_task(update, user_state)
            logger.info(f"[{chat_id}] Ambiente '{user_text}' registrado. Iniciando fila de tarefas de áudio.")
        else:
            await update.message.reply_text("Por favor, descreva o ambiente da gravação.")

    # ESTÁGIO 8+: Awaiting Audio Tasks (Vogais, Contagem)
    elif current_stage.startswith("awaiting_audio_"):
        if user_audio:
            task_type = user_state["metadata"]["current_audio_task"] # Pega a tarefa atual
            logger.info(f"[{chat_id}] Áudio detectado para a tarefa '{task_type}'. Baixando...")
            try:
                file_id = user_audio.file_id
                telegram_file = await context.bot.get_file(file_id)
                
                ext = 'ogg' 
                temp_file_name = f"/tmp/curumim_audio_{uuid.uuid4().hex}.{ext}"
                await telegram_file.download_to_drive(temp_file_name)
                logger.info(f"[{chat_id}] Áudio baixado e salvo temporariamente como: {temp_file_name}")

                r2_key = f"curumim_audios/{chat_id}/{user_state['metadata'].get('name', 'anon')}_{task_type}_{uuid.uuid4().hex}.{ext}"
                public_audio_url = upload_audio_to_r2(temp_file_name, r2_key, f"audio/{ext}") 
                
                if public_audio_url:
                    await update.message.reply_text(f"Áudio da {task_type.replace('_',' ')} recebido e salvo no R2! Obrigado.")
                    # Armazena a URL do áudio no metadata
                    if "audio_urls" not in user_state["metadata"]:
                        user_state["metadata"]["audio_urls"] = {}
                    user_state["metadata"]["audio_urls"][task_type] = public_audio_url
                    logger.info(f"[{chat_id}] Áudio salvo no R2: {public_audio_url}")
                else:
                    await update.message.reply_text("Áudio recebido, mas houve um problema ao salvar no R2. Por favor, tente novamente.")
                    logger.error(f"[{chat_id}] Falha ao salvar áudio no R2 para tarefa '{task_type}'.")
                
                if os.path.exists(temp_file_name):
                    os.remove(temp_file_name)
                    logger.info(f"[{chat_id}] Arquivo temporário '{temp_file_name}' removido.")

                # Solicita a próxima tarefa de áudio ou finaliza
                await request_next_audio_task(update, user_state)

            except Exception as e:
                logger.error(f"[{chat_id}] Erro ao processar áudio do Telegram para '{task_type}': {e}")
                await update.message.reply_text("Desculpe, tive um problema ao processar seu áudio. Poderia tentar novamente?")
        else:
            await update.message.reply_text("Não recebi um áudio. Por favor, grave e envie o áudio solicitado para a tarefa atual.")

    # ESTÁGIO FINAL: Finished
    elif current_stage == "finished":
        if user_text and user_text.lower() == "reiniciar":
            await start_command(update, context) # Reinicia o fluxo
        else:
            await update.message.reply_text("Sua contribuição está completa! Muito obrigado por ajudar a Angelia AI. Se quiser começar de novo, digite /start.")

    # --- Salvar Estado do Usuário (em memória) ---
    user_states[chat_id] = user_state 
    logger.info(f"[{chat_id}] Estado final após a lógica: {user_states[chat_id]}")


async def request_next_audio_task(update: Update, user_state: dict) -> None:
    """Gerencia a fila de tarefas de áudio e solicita a próxima."""
    chat_id = update.message.chat_id
    
    if user_state["tasks_queue"]:
        next_task = user_state["tasks_queue"].pop(0) # Pega a próxima tarefa da fila
        user_state["metadata"]["current_audio_task"] = next_task
        user_state["stage"] = f"awaiting_audio_{next_task}" # Atualiza o estágio

        if next_task == "vogal_a":
            await update.message.reply_text("Ótimo! Inspire fundo. Quando eu disser 'Já', por favor, diga 'Aaaaaa' por cerca de 5 segundos. Pronto? ... Já!")
        elif next_task == "vogal_i":
            await update.message.reply_text("Excelente. Agora, vamos fazer o mesmo com a vogal 'I'. Inspire fundo. Quando eu disser 'Já', por favor, diga 'Iiiiiii' por cerca de 5 segundos. Pronto? ... Já!")
        elif next_task == "vogal_o":
            await update.message.reply_text("Quase lá! Para a última vogal, inspire fundo. Quando eu disser 'Já', por favor, diga 'Oooooo' por cerca de 5 segundos. Pronto? ... Já!")
        elif next_task == "contagem_1_10":
            await update.message.reply_text("Para a próxima tarefa, por favor, inspire fundo. Quando eu disser 'Já', conte pausadamente de 1 a 10. Pronto? ... Já!")
        
        logger.info(f"[{chat_id}] Próxima tarefa de áudio solicitada: '{next_task}'.")
    else:
        # Todas as tarefas de áudio concluídas
        await update.message.reply_text(
            "Fantástico! Coletamos todas as suas amostras de voz. Sua contribuição é extremamente valiosa para a pesquisa de saúde da Angelia AI."
        )
        await update.message.reply_text(
            "Muito obrigado por participar! Seus dados foram salvos com sucesso."
            "\n\nDetalhes da sua sessão (anonimizados para pesquisa):"
            f"\nNome/ID: {user_state['metadata'].get('name', 'N/A')}"
            f"\nIdade: {user_state['metadata'].get('age', 'N/A')}"
            f"\nDiagnóstico: {user_state['metadata'].get('diagnosis', 'N/A')}"
            f"\nEstado Emocional: {user_state['metadata'].get('emotional_state', 'N/A')}"
            f"\nAmbiente: {user_state['metadata'].get('environment', 'N/A')}"
            "\n\nPara iniciar uma nova sessão, digite /start."
        )
        user_state["stage"] = "finished"
        logger.info(f"[{chat_id}] Todas as tarefas de áudio concluídas. Transicionou para 'finished'.")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Erros gerados por Updates."""
    logger.warning(f'Update "{update}" causou erro "{context.error}"')
    if update and update.message:
        await update.message.reply_text("Ops! Ocorreu um erro interno. Por favor, tente novamente ou digite /start para reiniciar.")


def main() -> None:
    """Inicia o bot."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Adiciona handlers para diferentes tipos de mensagens e comandos
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)) # Mensagens de texto que não são comandos
    application.add_handler(MessageHandler(filters.VOICE | filters.ATTACHMENT, message_handler)) # Mensagens de áudio ou outros anexos
    application.add_error_handler(error_handler)

    logger.info("Bot do Telegram Curumim iniciado. Escutando por mensagens...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    # Garante que o diretório /tmp exista para download temporário de áudios no Render/Replit
    os.makedirs('/tmp', exist_ok=True)
    main()