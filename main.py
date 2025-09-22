# curumim-telegram-bot/main.py
import os
from dotenv import load_dotenv
import aiofiles
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
    exit(1) # Sai se o token não estiver configurado

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
# Em produção, use um banco de dados para persistir o estado.
user_states = {} # { chat_id: {"stage": "initial", "metadata": {}} }

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
    """Envia uma mensagem de boas-vindas quando o comando /start é dado."""
    chat_id = update.message.chat_id
    sender_name = update.message.from_user.first_name

    # Reinicia o estado do usuário ao receber /start
    user_states[chat_id] = {"stage": "initial", "metadata": {}}
    user_state = user_states[chat_id]

    logger.info(f"[{chat_id}] Comando /start recebido de {sender_name}. Estado reiniciado: {user_state['stage']}.")

    await update.message.reply_text(
        f"Olá, {sender_name}! Eu sou Curumim, seu assistente para o projeto Angelia AI. Posso te ajudar a contribuir com sua voz para a pesquisa de saúde."
    )
    await update.message.reply_text("Para começar, digite 'COMEÇAR'.")
    user_state["stage"] = "waiting_start"
    logger.info(f"[{chat_id}] Transicionou para estágio 'waiting_start'.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envia uma mensagem de ajuda quando o comando /help é dado."""
    await update.message.reply_text("Envie 'COMEÇAR' para iniciar a coleta de áudios. "
                                    "Se já estiver no meio, continue de onde parou ou digite /start para reiniciar.")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processa todas as mensagens de texto e áudio."""
    chat_id = update.message.chat_id
    user_text = update.message.text
    user_audio = update.message.voice # Objeto de áudio do Telegram

    logger.info(f"[{chat_id}] Mensagem recebida: Texto='{user_text}', Áudio='{bool(user_audio)}'")

    # --- Obter/Inicializar Estado do Usuário ---
    if chat_id not in user_states:
        user_states[chat_id] = {"stage": "initial", "metadata": {}}
        logger.info(f"[{chat_id}] Novo usuário. Estado inicializado: {user_states[chat_id]}")

    user_state = user_states[chat_id]
    current_stage = user_state["stage"]
    logger.info(f"[{chat_id}] Estado atual antes da lógica: {current_stage} | Metadata: {user_state['metadata']}")

    # --- Lógica do Chatbot ---

    # ESTÁGIO 1: Initial (Já é tratado pelo /start, mas para garantir)
    if current_stage == "initial":
        logger.info(f"[{chat_id}] Entrou no estágio 'initial' via message_handler (deve ter vindo do /start).")
        await update.message.reply_text(
            "Olá! Eu sou Curumim, seu assistente para o projeto Angelia AI. Posso te ajudar a contribuir com sua voz para a pesquisa de saúde."
        )
        await update.message.reply_text("Para começar, digite 'COMEÇAR'.")
        user_state["stage"] = "waiting_start"
        logger.info(f"[{chat_id}] Transicionou para estágio 'waiting_start'.")

    # ESTÁGIO 2: Waiting for 'COMEÇAR'
    elif current_stage == "waiting_start":
        logger.info(f"[{chat_id}] Entrou no estágio 'waiting_start'.")
        if user_text and user_text.lower() == "começar":
            logger.info(f"[{chat_id}] Recebeu 'COMEÇAR'.")
            await update.message.reply_text(
                "Ótimo! Vamos começar. Para contribuir, por favor, grave e envie um áudio com uma *vogal 'A' sustentada* por 3 a 5 segundos (ex: Aaaaaa...).",
                parse_mode='Markdown' # Para formatar *texto*
            )
            await update.message.reply_text("Em seguida, vou pedir algumas informações.")
            user_state["stage"] = "waiting_audio_a"
            user_state["metadata"]["task_type"] = "vogal_a_sustentada" # Define o tipo de tarefa
            logger.info(f"[{chat_id}] Transicionou para estágio 'waiting_audio_a'.")
        else:
            logger.info(f"[{chat_id}] Mensagem inválida em 'waiting_start': '{user_text}'.")
            await update.message.reply_text("Entendi. Por favor, digite 'COMEÇAR' para iniciarmos.")

    # ESTÁGIO 3: Waiting for Audio 'A'
    elif current_stage == "waiting_audio_a":
        logger.info(f"[{chat_id}] Entrou no estágio 'waiting_audio_a'.")
        if user_audio: # Se um áudio foi enviado
            logger.info(f"[{chat_id}] Áudio detectado. Baixando...")
            try:
                # Baixa o arquivo de áudio
                file_id = user_audio.file_id
                telegram_file = await context.bot.get_file(file_id)

                # O Telegram geralmente envia áudios como 'voice' (ogg)
                ext = 'ogg' # Supondo que seja sempre ogg para voice messages
                temp_file_name = f"/tmp/curumim_audio_{uuid.uuid4().hex}.{ext}"
                await telegram_file.download_to_drive(temp_file_name)
                logger.info(f"[{chat_id}] Áudio baixado e salvo temporariamente como: {temp_file_name}")

                r2_key = f"curumim_audios/{chat_id}/{user_state['metadata'].get('task_type', 'unknown_task')}_{uuid.uuid4().hex}.{ext}"
                public_audio_url = upload_audio_to_r2(temp_file_name, r2_key, f"audio/{ext}") # Content-Type

                if public_audio_url:
                    await update.message.reply_text(f"Áudio recebido e salvo no R2! Obrigado pela sua contribuição.")
                    logger.info(f"[{chat_id}] Áudio salvo no R2: {public_audio_url}")
                else:
                    await update.message.reply_text("Áudio recebido, mas houve um problema ao salvar no R2. Por favor, tente novamente.")
                    logger.error(f"[{chat_id}] Falha ao salvar áudio no R2.")

                # Limpar arquivo temporário
                if os.path.exists(temp_file_name):
                    os.remove(temp_file_name)
                    logger.info(f"[{chat_id}] Arquivo temporário '{temp_file_name}' removido.")

                # Próxima etapa: coletar metadados
                await update.message.reply_text("Para complementar sua contribuição, por favor, me diga sua *idade* (apenas números).", parse_mode='Markdown')
                user_state["stage"] = "waiting_age"
                logger.info(f"[{chat_id}] Transicionou para estágio 'waiting_age'.")
            except Exception as e:
                logger.error(f"[{chat_id}] Erro ao processar áudio do Telegram: {e}")
                await update.message.reply_text("Desculpe, tive um problema ao processar seu áudio. Poderia tentar novamente?")
        else:
            logger.info(f"[{chat_id}] Não recebeu áudio no estágio 'waiting_audio_a'. Mensagem: '{user_text}'.")
            await update.message.reply_text("Não recebi um áudio. Por favor, grave e envie o áudio da vogal 'A' sustentada.")

    # ESTÁGIO 4: Waiting for Age
    elif current_stage == "waiting_age":
        logger.info(f"[{chat_id}] Entrou no estágio 'waiting_age'.")
        if user_text and user_text.isdigit():
            age = int(user_text)
            user_state["metadata"]["age"] = age
            await update.message.reply_text("Idade registrada! Agora, qual é o seu *gênero*? (Ex: Masculino, Feminino, Outro)", parse_mode='Markdown')
            user_state["stage"] = "waiting_gender"
            logger.info(f"[{chat_id}] Idade '{age}' registrada. Transicionou para estágio 'waiting_gender'.")
        else:
            logger.info(f"[{chat_id}] Idade inválida em 'waiting_age': '{user_text}'.")
            await update.message.reply_text("Por favor, digite sua idade em números.")

    # ESTÁGIO 5: Waiting for Gender
    elif current_stage == "waiting_gender":
        logger.info(f"[{chat_id}] Entrou no estágio 'waiting_gender'.")
        if user_text:
            gender = user_text.strip().lower()
            user_state["metadata"]["gender"] = gender
            await update.message.reply_text("Gênero registrado! Sua contribuição está completa. Muito obrigado por ajudar a Angelia AI!")
            await update.message.reply_text(f"Seus dados coletados: {user_state['metadata']}") # Depuração final
            user_state["stage"] = "finished" 
            logger.info(f"[{chat_id}] Gênero '{gender}' registrado. Transicionou para estágio 'finished'.")
        else:
            logger.info(f"[{chat_id}] Gênero inválido em 'waiting_gender': '{user_text}'.")
            await update.message.reply_text("Por favor, digite seu gênero.")

    # ESTÁGIO 6: Finished (Oferece reiniciar)
    elif current_stage == "finished":
        logger.info(f"[{chat_id}] Entrou no estágio 'finished'.")
        if user_text and user_text.lower() == "reiniciar":
            logger.info(f"[{chat_id}] Recebeu 'REINICIAR'. Reiniciando conversa.")
            user_states[chat_id] = {"stage": "initial", "metadata": {}} # Reinicia o estado
            await update.message.reply_text("Reiniciando a conversa. " +
                                            "Olá! Eu sou Curumim, seu assistente para o projeto Angelia AI. Posso te ajudar a contribuir com sua voz para a pesquisa de saúde." +
                                            " Para começar, digite 'COMEÇAR'.")
            user_state["stage"] = "waiting_start" # Transiciona para o estágio correto após o reinício
        else:
            logger.info(f"[{chat_id}] Mensagem em 'finished': '{user_text}'.")
            await update.message.reply_text("Já coletamos sua contribuição! Se quiser começar de novo, digite 'REINICIAR' ou /start.")

    # --- Salvar Estado do Usuário ---
    user_states[chat_id] = user_state # Garante que o estado seja salvo no dicionário (mesmo que em memória)
    logger.info(f"[{chat_id}] Estado final após a lógica: {user_states[chat_id]}")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Erros gerados por Updates."""
    logger.warning(f'Update "{update}" causou erro "{context.error}"')


def main() -> None:
    """Inicia o bot."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Adiciona handlers para diferentes tipos de mensagens e comandos
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)) # Mensagens de texto que não são comandos
    application.add_handler(MessageHandler(filters.VOICE | filters.ATTACHMENT, message_handler)) # Mensagens de áudio ou outros anexos
    application.add_error_handler(error_handler)

    # Inicia o bot com polling (método simples para rodar localmente ou em servidor)
    logger.info("Bot do Telegram Curumim iniciado. Escutando por mensagens...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()