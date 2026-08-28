print("========================================")
print("REQUIEM INICIANDO...")
print("========================================")

from dotenv import load_dotenv
import os

load_dotenv()

print("DISCORD_TOKEN encontrado:", bool(os.getenv("DISCORD_TOKEN")))

import asyncio
import json
import logging
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from database.boosts import addBoost, changeBoost
from database.roles import get_role_id, set_role_id

# ============================================================
# CONFIGURAÇÃO
# ============================================================
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN não foi encontrado no arquivo .env.")

intents = discord.Intents.all()
# Necessário para receber o evento on_member_join.
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix=["-", "impulso "], intents=intents)

MAIN_GUILD_ID = 1528960946825855037
DEFAULT_GIVEAWAY_CHANNEL_ID = 1532562480851583077
GENERAL_CHAT_CHANNEL_ID = 1542466794000879626
INVITES_LOG_CHANNEL_ID = 1538324069211054160
WINNERS_ANNOUNCEMENT_CHANNEL_ID = 1532562696652849322
LOG_CHANNEL_ID = 1528961214724575354

GIVEAWAY_ADMIN_ROLE_IDS = [
    1529370748303577118,
    1529370672311308358,
    1529317379677356152,
    1529318511225344151,
    1529341829332471848,
]

# Único cargo autorizado a criar sorteios e a enviar os painéis (embeds) de suporte/FAQ.
SORTEIO_EMBED_ROLE_ID = 1529054909360508958

EXTRA_ENTRY_ROLES = {
    1531974356517785640: 2,
    1531974798568067142: 3,
    1529134166266875955: 2,
    1538278143142666412: 3,
}

GIVEAWAY_COLOR = 0x000000
FOOTER_TEXT = "ⓘ Requiem"
FOOTER_ICON = (
    "https://media.discordapp.net/attachments/1541462822146277376/"
    "1542601495885652119/88FA09EC-88B4-4D6C-9E01-1959F7F7B9AF.png"
    "?ex=6a91d318&is=6a908198&hm=9cecf833a12df3c9533d6b443594f4770ec73ed6e72e1a6419ec9c287b29bdc9"
    "&=&format=webp&quality=lossless&width=968&height=968"
)
GIVEAWAY_FOOTER_IMAGE = (
    "https://media.discordapp.net/attachments/1528974980690477077/"
    "1538947435760713808/image.png"
    "?ex=6a8487fc&is=6a83367c&hm=926b84c9eccac4adb362877d02eb29780cc02ac091bf39c0801679b08222a8db"
    "&=&format=webp&quality=lossless&width=750&height=53"
)

# Somente emojis personalizados do Requiem. Nenhum emoji Unicode é
# inserido automaticamente nos Giveaways.
EMOJI_REQUIEM = "<:z_requiem:1539260704799072296>"
EMOJI_REQUIEM_ALT = "<:requiem:1533006344980791346>"
EMOJI_Z_REQUIEM = "<:z_requiem:1539260676206231583>"
EMOJI_ANIMATED_REQUIEM = "<a:z_requiem:1533134533275422944>"

GIVEAWAY_DATABASE = "giveaways.db"
giveaway_tasks = {}
giveaways_restored = False
bot_ready_initialized = False
invites_cache = {}
ultima_mensagem_horario = None
ultimo_bump_tempo = None
aviso_bump_enviado = False

# ============================================================
# BANCO DE DADOS
# ============================================================
def giveaway_db():
    return sqlite3.connect(GIVEAWAY_DATABASE)


def setup_giveaway_database():
    db = giveaway_db()
    cur = db.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS giveaways (
            giveaway_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER,
            created_by INTEGER NOT NULL,
            prize TEXT NOT NULL,
            winner_count INTEGER NOT NULL,
            start_time INTEGER NOT NULL,
            end_time INTEGER NOT NULL,
            requirements TEXT,
            announcement_channel_id INTEGER,
            mention_role_id INTEGER,
            everyone INTEGER DEFAULT 0,
            here INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            winners TEXT DEFAULT '',
            created_at INTEGER NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS giveaway_participants (
            giveaway_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            entries INTEGER DEFAULT 1,
            joined_at INTEGER NOT NULL,
            PRIMARY KEY (giveaway_id, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS giveaway_message_counts (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)

    # Migração automática para giveaways.db antigos.
    existing = {row[1] for row in cur.execute("PRAGMA table_info(giveaways)").fetchall()}
    new_columns = {
        "embed_title": "TEXT",
        "embed_description": "TEXT",
        "embed_image_url": "TEXT",
        "embed_thumbnail_url": "TEXT",
        "button_label": "TEXT",
        "button_emoji": "TEXT",
        "content_message": "TEXT",
    }
    for name, typ in new_columns.items():
        if name not in existing:
            cur.execute(f"ALTER TABLE giveaways ADD COLUMN {name} {typ}")

    db.commit()
    db.close()


setup_giveaway_database()

# ============================================================
# UTILITÁRIOS
# ============================================================
def now_ts():
    return int(datetime.now(timezone.utc).timestamp())


def parse_duration(value):
    if not value:
        return None
    match = re.fullmatch(r"(\d+)\s*(m|min|h|d|dia|sem|semana)", value.lower().strip())
    if not match:
        return None
    number = int(match.group(1))
    unit = match.group(2)
    if number <= 0:
        return None
    if unit in ("m", "min"):
        return number * 60
    if unit == "h":
        return number * 3600
    if unit in ("d", "dia"):
        return number * 86400
    if unit in ("sem", "semana"):
        return number * 604800
    return None


def parse_tempo(tempo_str):
    if not tempo_str:
        return None
    match = re.fullmatch(r"(\d+)\s*(seg|min|m|h|d|dia|sem|mes)", tempo_str.lower().strip())
    if not match:
        return None
    valor = int(match.group(1))
    unidade = match.group(2)
    if valor <= 0:
        return None
    if unidade == "seg":
        return timedelta(seconds=valor)
    if unidade in ("min", "m"):
        return timedelta(minutes=valor)
    if unidade == "h":
        return timedelta(hours=valor)
    if unidade in ("d", "dia"):
        return timedelta(days=valor)
    if unidade == "sem":
        return timedelta(weeks=valor)
    if unidade == "mes":
        return timedelta(days=valor * 30)
    return None


def get_extra_entries(member):
    entries = 1
    for role_id, bonus in EXTRA_ENTRY_ROLES.items():
        if member.get_role(role_id):
            entries += bonus
    return entries


def is_giveaway_admin(member):
    return (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
        or any(role.id in GIVEAWAY_ADMIN_ROLE_IDS for role in member.roles)
    )


def row_to_dict(row):
    columns = [
        "giveaway_id", "guild_id", "channel_id", "message_id", "created_by",
        "prize", "winner_count", "start_time", "end_time", "requirements",
        "announcement_channel_id", "mention_role_id", "everyone", "here",
        "status", "winners", "created_at", "embed_title", "embed_description",
        "embed_image_url", "embed_thumbnail_url", "button_label", "button_emoji",
        "content_message",
    ]
    return dict(zip(columns, row))


def load_giveaway(giveaway_id):
    db = giveaway_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM giveaways WHERE giveaway_id = ?", (giveaway_id,))
    row = cur.fetchone()
    db.close()
    if not row:
        return None
    # A tabela pode ter exatamente as colunas atuais após a migração.
    return row_to_dict(row)


def parse_requirements(raw):
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def replace_placeholders(text, prize, winners, participants, end_time):
    text = text or ""
    values = {
        "{premio}": prize,
        "{vencedores}": str(winners),
        "{participantes}": str(participants),
        "{termina}": f"<t:{end_time}:F>",
        "{tempo}": f"<t:{end_time}:R>",
    }
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def valid_requiem_emoji(guild, raw):
    if not raw:
        return None
    try:
        partial = discord.PartialEmoji.from_str(raw.strip())
    except Exception:
        return None
    if not partial.id:
        return None
    emoji = guild.get_emoji(partial.id)
    if emoji is None or "requiem" not in emoji.name.lower():
        return None
    return str(emoji)


def requirements_text(requirements, guild):
    result = []
    messages = int(requirements.get("messages", 0) or 0)
    if messages > 0:
        result.append(f"{messages} mensagens em <#{GENERAL_CHAT_CHANNEL_ID}>")

    required_role = requirements.get("required_role")
    if required_role:
        role = guild.get_role(int(required_role))
        result.append(f"Possuir o cargo {role.mention if role else f'<@&{required_role}>'}")

    forbidden_role = requirements.get("forbidden_role")
    if forbidden_role:
        role = guild.get_role(int(forbidden_role))
        result.append(f"Não possuir o cargo {role.mention if role else f'<@&{forbidden_role}>'}")

    required_server = requirements.get("required_server_id")
    if required_server:
        other_guild = bot.get_guild(int(required_server))
        name = other_guild.name if other_guild else f"ID {required_server}"
        result.append(f"Estar no servidor **{name}**")

    required_invites = int(requirements.get("invites", 0) or 0)
    if required_invites > 0:
        result.append(f"Ter pelo menos **{required_invites} invites** no Requiem")

    return result


async def get_user_invite_count(guild, user_id):
    """Conta os usos dos convites criados pelo usuário no servidor."""
    try:
        invites = await guild.invites()
    except (discord.Forbidden, discord.HTTPException) as exc:
        logging.warning("Não foi possível consultar convites de %s: %s", guild.id, exc)
        return None
    total = 0
    for invite in invites:
        if invite.inviter and invite.inviter.id == user_id:
            total += invite.uses or 0
    return total


async def check_giveaway_requirements(member, giveaway):
    failed = []
    req = parse_requirements(giveaway.get("requirements")) if isinstance(giveaway.get("requirements"), str) else giveaway.get("requirements", {})

    required_role = req.get("required_role")
    if required_role:
        role = member.guild.get_role(int(required_role))
        if role and role not in member.roles:
            failed.append(f"{EMOJI_Z_REQUIEM} Você precisa possuir o cargo {role.mention}.")

    forbidden_role = req.get("forbidden_role")
    if forbidden_role:
        role = member.guild.get_role(int(forbidden_role))
        if role and role in member.roles:
            failed.append(f"{EMOJI_Z_REQUIEM} Você não pode possuir o cargo {role.mention}.")

    messages = int(req.get("messages", 0) or 0)
    if messages > 0:
        db = giveaway_db()
        cur = db.cursor()
        cur.execute("SELECT count FROM giveaway_message_counts WHERE guild_id = ? AND user_id = ?", (member.guild.id, member.id))
        row = cur.fetchone()
        db.close()
        count = row[0] if row else 0
        if count < messages:
            failed.append(f"{EMOJI_Z_REQUIEM} Mensagens no chat geral: **{count}/{messages}**.")

    required_server = req.get("required_server_id")
    if required_server:
        other_guild = bot.get_guild(int(required_server))
        if other_guild is None:
            failed.append(f"{EMOJI_Z_REQUIEM} O bot não está no servidor exigido, então não é possível verificar sua entrada.")
        else:
            try:
                other_member = other_guild.get_member(member.id) or await other_guild.fetch_member(member.id)
            except (discord.NotFound, discord.HTTPException, discord.Forbidden):
                other_member = None
            if other_member is None:
                failed.append(f"{EMOJI_Z_REQUIEM} Você precisa estar no servidor **{other_guild.name}**.")

    invites_required = int(req.get("invites", 0) or 0)
    if invites_required > 0:
        invite_count = await get_user_invite_count(member.guild, member.id)
        if invite_count is None:
            failed.append(f"{EMOJI_Z_REQUIEM} Não foi possível verificar seus invites agora.")
        elif invite_count < invites_required:
            failed.append(f"{EMOJI_Z_REQUIEM} Invites: **{invite_count}/{invites_required}**.")

    return not failed, failed


def build_giveaway_embed(data, guild, participants=0, status=None):
    status = status or data.get("status", "active")
    req = parse_requirements(data.get("requirements")) if isinstance(data.get("requirements"), str) else data.get("requirements", {})
    title = data.get("embed_title") or "Sorteio"
    description = data.get("embed_description") or "Participe do sorteio abaixo."
    description = replace_placeholders(description, data["prize"], data["winner_count"], participants, data["end_time"])

    if status == "finished":
        description += "\n\n" + f"{EMOJI_REQUIEM} Este sorteio foi encerrado."
    elif status == "cancelled":
        description += "\n\n" + f"{EMOJI_Z_REQUIEM} Este sorteio foi cancelado."

    embed = discord.Embed(title=title[:256], description=description[:4096], color=GIVEAWAY_COLOR)
    embed.add_field(name="Prêmio", value=data["prize"][:1024], inline=False)
    embed.add_field(name="Vencedores", value=str(data["winner_count"]), inline=True)
    embed.add_field(name="Participantes", value=str(participants), inline=True)
    embed.add_field(name="Término", value="Encerrado" if status != "active" else f"<t:{data['end_time']}:R>", inline=True)

    req_lines = requirements_text(req, guild)
    if req_lines:
        embed.add_field(name="Requisitos", value="\n".join(f"- {line}" for line in req_lines), inline=False)

    image_url = (data.get("embed_image_url") or "").strip()
    thumbnail_url = (data.get("embed_thumbnail_url") or "").strip()
    if image_url:
        embed.set_image(url=image_url)
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    return embed


def giveaway_content(data, guild):
    content = (data.get("content_message") or "").strip()
    mention_role_id = data.get("mention_role_id")
    if mention_role_id:
        role = guild.get_role(int(mention_role_id))
        if role:
            content = f"{role.mention} {content}".strip()
    if int(data.get("everyone", 0) or 0):
        content = f"@everyone {content}".strip()
    if int(data.get("here", 0) or 0):
        content = f"@here {content}".strip()
    return content or None

# ============================================================
# CONTADOR DE MENSAGENS
# ============================================================
async def update_giveaway_message_counter(message):
    if not message.guild or message.author.bot or message.channel.id != GENERAL_CHAT_CHANNEL_ID:
        return
    db = giveaway_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO giveaway_message_counts (guild_id, user_id, count)
        VALUES (?, ?, 1)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET count = count + 1
    """, (message.guild.id, message.author.id))
    db.commit()
    db.close()

# ============================================================
# VIEWS DO GIVEAWAY
# ============================================================
class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id, guild):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id == "giveaway_participate":
                    child.custom_id = f"giveaway:participate:{giveaway_id}"
                    emoji = guild.get_emoji(1538409239733731348)
                    if emoji:
                        child.emoji = emoji
                elif child.custom_id == "giveaway_leave":
                    child.custom_id = f"giveaway:leave:{giveaway_id}"
                    emoji = guild.get_emoji(1533006344980791346)
                    if emoji:
                        child.emoji = emoji

    @discord.ui.button(label="Participar", style=discord.ButtonStyle.secondary, custom_id="giveaway_participate")
    async def participate_button(self, interaction, button):
        await handle_giveaway_join(interaction, self.giveaway_id)

    @discord.ui.button(label="Sair", style=discord.ButtonStyle.secondary, custom_id="giveaway_leave")
    async def leave_button(self, interaction, button):
        await handle_giveaway_leave(interaction, self.giveaway_id)


class EditGiveawayModal(discord.ui.Modal, title="Editar Giveaway"):
    def __init__(self, parent_view):
        super().__init__(timeout=300)
        self.parent_view = parent_view
        d = parent_view.data
        self.mensagem = discord.ui.TextInput(label="Mensagem acima do embed", style=discord.TextStyle.paragraph, required=False, max_length=2000, default=d.get("content_message", ""))
        self.titulo = discord.ui.TextInput(label="Título do embed", required=True, max_length=256, default=d.get("embed_title", "Sorteio"))
        self.descricao = discord.ui.TextInput(label="Descrição do embed", style=discord.TextStyle.paragraph, required=True, max_length=4000, default=d.get("embed_description", ""))
        self.imagem = discord.ui.TextInput(label="URL da imagem", required=False, max_length=1000, default=d.get("embed_image_url", ""))
        self.thumbnail = discord.ui.TextInput(label="URL da thumbnail", required=False, max_length=1000, default=d.get("embed_thumbnail_url", ""))
        self.add_item(self.mensagem)
        self.add_item(self.titulo)
        self.add_item(self.descricao)
        self.add_item(self.imagem)
        self.add_item(self.thumbnail)

    async def on_submit(self, interaction):
        d = self.parent_view.data
        d["content_message"] = self.mensagem.value.strip()
        d["embed_title"] = self.titulo.value.strip() or "Sorteio"
        d["embed_description"] = self.descricao.value.strip() or "Participe do sorteio abaixo."
        d["embed_image_url"] = self.imagem.value.strip()
        d["embed_thumbnail_url"] = self.thumbnail.value.strip()
        embed = build_giveaway_embed(d, interaction.guild, 0, "active")
        await interaction.response.edit_message(content=giveaway_content(d, interaction.guild), embed=embed, view=self.parent_view)


class GiveawayPreviewView(discord.ui.View):
    def __init__(self, author_id, data):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.data = data
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Apenas quem criou o preview pode usar estes botões.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Editar mensagem", style=discord.ButtonStyle.secondary)
    async def edit_button(self, interaction, button):
        await interaction.response.send_modal(EditGiveawayModal(self))

    @discord.ui.button(label="Enviar sorteio", style=discord.ButtonStyle.secondary)
    async def send_button(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        await create_giveaway_from_preview(interaction, self.data, self)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction, button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"{EMOJI_Z_REQUIEM} Preview cancelado.", embed=None, view=self)
        self.stop()


async def refresh_giveaway_message(giveaway_id):
    data = load_giveaway(giveaway_id)
    if not data:
        return
    guild = bot.get_guild(data["guild_id"])
    if not guild or not data.get("message_id"):
        return
    channel = guild.get_channel(data["channel_id"])
    if not channel:
        return
    db = giveaway_db()
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM giveaway_participants WHERE giveaway_id = ?", (giveaway_id,))
    participants = cur.fetchone()[0]
    db.close()
    try:
        message = await channel.fetch_message(data["message_id"])
        await message.edit(embed=build_giveaway_embed(data, guild, participants), view=GiveawayView(giveaway_id, guild))
    except discord.NotFound:
        logging.warning(
            "Mensagem do Giveaway #%s não existe mais no Discord (message_id=%s).",
            giveaway_id, data["message_id"]
        )
    except discord.HTTPException as exc:
        logging.warning("Não foi possível atualizar Giveaway #%s: %s", giveaway_id, exc)


async def handle_giveaway_join(interaction, giveaway_id):
    if not interaction.guild:
        return
    data = load_giveaway(giveaway_id)
    if not data:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Giveaway não encontrado.", ephemeral=True)
        return
    if data["status"] != "active" or now_ts() >= data["end_time"]:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Este Giveaway não está mais ativo.", ephemeral=True)
        return
    eligible, failed = await check_giveaway_requirements(interaction.user, data)
    if not eligible:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não pode participar deste Giveaway.\n\n" + "\n".join(failed), ephemeral=True)
        return
    db = giveaway_db()
    cur = db.cursor()
    cur.execute("SELECT 1 FROM giveaway_participants WHERE giveaway_id = ? AND user_id = ?", (giveaway_id, interaction.user.id))
    if cur.fetchone():
        db.close()
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você já está participando deste Giveaway.", ephemeral=True)
        return
    entries = get_extra_entries(interaction.user)
    cur.execute("INSERT INTO giveaway_participants (giveaway_id, user_id, entries, joined_at) VALUES (?, ?, ?, ?)", (giveaway_id, interaction.user.id, entries, now_ts()))
    db.commit()
    db.close()
    await interaction.response.send_message(f"{EMOJI_REQUIEM} Você entrou no Giveaway. Entradas: **{entries}**.", ephemeral=True)
    await refresh_giveaway_message(giveaway_id)


async def handle_giveaway_leave(interaction, giveaway_id):
    data = load_giveaway(giveaway_id)
    if not data:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Giveaway não encontrado.", ephemeral=True)
        return
    db = giveaway_db()
    cur = db.cursor()
    cur.execute("DELETE FROM giveaway_participants WHERE giveaway_id = ? AND user_id = ?", (giveaway_id, interaction.user.id))
    removed = cur.rowcount
    db.commit()
    db.close()
    if not removed:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não está participando deste Giveaway.", ephemeral=True)
        return
    await interaction.response.send_message(f"{EMOJI_REQUIEM_ALT} Você saiu do Giveaway.", ephemeral=True)
    await refresh_giveaway_message(giveaway_id)

# ============================================================
# CRIAÇÃO / FINALIZAÇÃO
# ============================================================
async def create_giveaway_from_preview(interaction, data, preview_view):
    guild = interaction.guild
    channel = guild.get_channel(data["channel_id"])
    if not channel:
        await interaction.followup.send(f"{EMOJI_Z_REQUIEM} Canal do Giveaway não encontrado.", ephemeral=True)
        return

    # Valida URLs antes de gravar.
    for key in ("embed_image_url", "embed_thumbnail_url"):
        value = (data.get(key) or "").strip()
        if value and not re.match(r"^https?://", value, re.I):
            await interaction.followup.send(f"{EMOJI_Z_REQUIEM} A URL de {key} precisa começar com http:// ou https://.", ephemeral=True)
            return

    req_json = json.dumps(data["requirements"], ensure_ascii=False)
    created = now_ts()
    db = giveaway_db()
    cur = db.cursor()
    insert_columns = (
        "guild_id", "channel_id", "message_id", "created_by", "prize", "winner_count",
        "start_time", "end_time", "requirements", "announcement_channel_id",
        "mention_role_id", "everyone", "here", "status", "winners", "created_at",
        "embed_title", "embed_description", "embed_image_url", "embed_thumbnail_url",
        "button_label", "button_emoji", "content_message"
    )
    insert_values = (
        guild.id, channel.id, None, interaction.user.id, data["prize"], data["winner_count"],
        data["start_time"], data["end_time"], req_json, WINNERS_ANNOUNCEMENT_CHANNEL_ID,
        data.get("mention_role_id"), int(data.get("everyone", False)), int(data.get("here", False)),
        "active", "", created, data["embed_title"], data["embed_description"],
        data.get("embed_image_url", ""), data.get("embed_thumbnail_url", ""),
        data.get("button_label", "Participar"), data.get("button_emoji"),
        data.get("content_message", "")
    )
    if len(insert_columns) != len(insert_values):
        db.close()
        raise RuntimeError(
            f"Erro interno ao criar Giveaway: {len(insert_columns)} colunas para "
            f"{len(insert_values)} valores."
        )
    placeholders = ", ".join("?" for _ in insert_values)
    cur.execute(
        f"INSERT INTO giveaways ({', '.join(insert_columns)}) VALUES ({placeholders})",
        insert_values
    )
    giveaway_id = cur.lastrowid
    db.commit()
    db.close()

    data["giveaway_id"] = giveaway_id
    data["requirements"] = req_json
    try:
        message = await channel.send(
            content=giveaway_content(data, guild),
            embed=build_giveaway_embed(data, guild, 0, "active"),
            view=GiveawayView(giveaway_id, guild),
            allowed_mentions=discord.AllowedMentions(everyone=True, roles=True)
        )
    except Exception as exc:
        db = giveaway_db()
        cur = db.cursor()
        cur.execute("UPDATE giveaways SET status = 'cancelled' WHERE giveaway_id = ?", (giveaway_id,))
        db.commit()
        db.close()
        await interaction.followup.send(f"{EMOJI_Z_REQUIEM} Não foi possível enviar o Giveaway.\n```{exc}```", ephemeral=True)
        return

    db = giveaway_db()
    cur = db.cursor()
    cur.execute("UPDATE giveaways SET message_id = ? WHERE giveaway_id = ?", (message.id, giveaway_id))
    db.commit()
    db.close()

    task = asyncio.create_task(schedule_giveaway(giveaway_id, data["end_time"]))
    giveaway_tasks[giveaway_id] = task
    for child in preview_view.children:
        child.disabled = True
    try:
        await preview_view.message.edit(content=f"{EMOJI_REQUIEM} Giveaway **#{giveaway_id}** enviado em {channel.mention}.", embed=build_giveaway_embed(data, guild, 0), view=preview_view)
    except Exception:
        pass
    preview_view.stop()


async def finish_giveaway(giveaway_id):
    data = load_giveaway(giveaway_id)
    if not data or data["status"] != "active":
        return
    db = giveaway_db()
    cur = db.cursor()
    cur.execute("SELECT user_id, entries FROM giveaway_participants WHERE giveaway_id = ?", (giveaway_id,))
    participants = cur.fetchall()
    db.close()

    pool = []
    for user_id, entries in participants:
        pool.extend([user_id] * max(1, int(entries)))
    winners = []
    max_winners = min(data["winner_count"], len(set(pool)))
    while pool and len(winners) < max_winners:
        winner = random.choice(pool)
        winners.append(winner)
        pool = [uid for uid in pool if uid != winner]

    db = giveaway_db()
    cur = db.cursor()
    cur.execute("UPDATE giveaways SET status = 'finished', winners = ? WHERE giveaway_id = ?", (",".join(map(str, winners)), giveaway_id))
    db.commit()
    db.close()
    giveaway_tasks.pop(giveaway_id, None)

    guild = bot.get_guild(data["guild_id"])
    if not guild:
        return
    channel = guild.get_channel(data["channel_id"])
    if channel and data.get("message_id"):
        try:
            message = await channel.fetch_message(data["message_id"])
            await message.edit(embed=build_giveaway_embed(data, guild, len(participants), "finished"), view=None)
        except discord.NotFound:
            logging.warning(
                "Mensagem do Giveaway #%s não existe mais no Discord (message_id=%s).",
                giveaway_id, data["message_id"]
            )
        except discord.HTTPException as exc:
            logging.warning("Erro ao editar Giveaway encerrado #%s: %s", giveaway_id, exc)

    announcement = guild.get_channel(data.get("announcement_channel_id") or WINNERS_ANNOUNCEMENT_CHANNEL_ID)
    if winners:
        mentions = []
        for uid in winners:
            member = guild.get_member(uid)
            if member:
                mentions.append(member.mention)
                try:
                    await member.send(f"{EMOJI_REQUIEM} Você venceu um Giveaway no **Requiem**.\nPrêmio: **{data['prize']}**")
                except Exception:
                    pass
        if announcement:
            await announcement.send(f"{EMOJI_REQUIEM} **Sorteio encerrado**\n\nPrêmio: **{data['prize']}**\nVencedores:\n" + "\n".join(mentions))
    elif announcement:
        await announcement.send(f"{EMOJI_Z_REQUIEM} O Giveaway de **{data['prize']}** terminou sem participantes elegíveis.")


async def schedule_giveaway(giveaway_id, end_time):
    try:
        await asyncio.sleep(max(0, end_time - datetime.now(timezone.utc).timestamp()))
        await finish_giveaway(giveaway_id)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logging.error("Erro na tarefa do Giveaway #%s: %s", giveaway_id, exc)
    finally:
        giveaway_tasks.pop(giveaway_id, None)


async def restore_giveaways():
    global giveaways_restored
    if giveaways_restored:
        return
    giveaways_restored = True
    db = giveaway_db()
    cur = db.cursor()
    cur.execute("SELECT giveaway_id, guild_id, message_id, end_time FROM giveaways WHERE status = 'active'")
    active = cur.fetchall()
    db.close()
    for giveaway_id, guild_id, message_id, end_time in active:
        guild = bot.get_guild(guild_id)
        if guild and message_id:
            bot.add_view(GiveawayView(giveaway_id, guild), message_id=message_id)
        if giveaway_id not in giveaway_tasks:
            giveaway_tasks[giveaway_id] = asyncio.create_task(schedule_giveaway(giveaway_id, end_time))
    logging.info("Giveaways restaurados: %s", len(active))

# ============================================================
# /GIVEAWAY CRIAR
# ============================================================
giveaway_group = app_commands.Group(name="giveaway", description="Sistema de Giveaways do Requiem")
bot.tree.add_command(giveaway_group, guild=discord.Object(id=MAIN_GUILD_ID))


@giveaway_group.command(name="criar", description="Cria um Giveaway e mostra um preview antes de enviar")
@app_commands.describe(
    premio="Prêmio do sorteio",
    duracao="Ex.: 10m, 1h, 1d",
    vencedores="Quantidade de vencedores",
    titulo="Título do embed",
    descricao="Descrição do embed. Placeholders: {premio}, {vencedores}, {participantes}, {termina}, {tempo}",
    mensagem="Mensagem acima do embed",
    canal="Canal onde o Giveaway será enviado",
    mensagens="Mensagens mínimas no chat geral",
    servidor_id="ID de outro servidor que o participante precisa estar",
    invites="Quantidade mínima de invites no Requiem",
    cargo="Cargo obrigatório",
    cargo_proibido="Cargo que o participante não pode possuir"
)
async def giveaway_criar(
    interaction: discord.Interaction,
    premio: str,
    duracao: str,
    vencedores: int = 1,
    titulo: str = "Sorteio",
    descricao: str = "Participe do sorteio abaixo.\n\nUse os botões para participar.",
    mensagem: str = "",
    canal: discord.TextChannel = None,
    mensagens: int = 0,
    servidor_id: str = "",
    invites: int = 0,
    cargo: discord.Role = None,
    cargo_proibido: discord.Role = None,
):
    if not interaction.guild:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Use este comando dentro do servidor.", ephemeral=True)
        return
    if interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Este sistema só pode ser usado no servidor principal do Requiem.", ephemeral=True)
        return
    if not any(role.id == SORTEIO_EMBED_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não possui permissão para administrar Giveaways.", ephemeral=True)
        return
    if not premio.strip():
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Informe um prêmio válido.", ephemeral=True)
        return
    if vencedores < 1:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Deve existir pelo menos 1 vencedor.", ephemeral=True)
        return
    if mensagens < 0 or invites < 0:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Os requisitos numéricos não podem ser negativos.", ephemeral=True)
        return
    seconds = parse_duration(duracao)
    if seconds is None or seconds > 7 * 86400:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Duração inválida. Use, por exemplo, `10m`, `1h` ou `1d`. Máximo: 7 dias.", ephemeral=True)
        return

    required_server_id = None
    if servidor_id.strip():
        if not servidor_id.strip().isdigit():
            await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} O ID do servidor precisa ser numérico.", ephemeral=True)
            return
        required_server_id = int(servidor_id.strip())
        if not bot.get_guild(required_server_id):
            await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} O bot não está no servidor `{required_server_id}`. Para verificar esse requisito, o bot precisa estar nos dois servidores.", ephemeral=True)
            return

    channel = canal or interaction.guild.get_channel(DEFAULT_GIVEAWAY_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Canal do Giveaway não encontrado.", ephemeral=True)
        return

    # O preview não grava nada no banco nem envia para o canal final.
    start = now_ts()
    end = start + seconds
    requirements = {}
    if mensagens:
        requirements["messages"] = mensagens
    if cargo:
        requirements["required_role"] = cargo.id
    if cargo_proibido:
        requirements["forbidden_role"] = cargo_proibido.id
    if required_server_id:
        requirements["required_server_id"] = required_server_id
    if invites:
        requirements["invites"] = invites

    data = {
        "guild_id": interaction.guild.id,
        "channel_id": channel.id,
        "created_by": interaction.user.id,
        "prize": premio.strip(),
        "winner_count": vencedores,
        "start_time": start,
        "end_time": end,
        "requirements": requirements,
        "embed_title": titulo.strip() or "Sorteio",
        "embed_description": descricao.strip() or "Participe do sorteio abaixo.",
        "embed_image_url": "",
        "embed_thumbnail_url": "",
        "button_label": "Participar",
        "button_emoji": EMOJI_REQUIEM,
        "content_message": mensagem.strip(),
        "mention_role_id": None,
        "everyone": False,
        "here": False,
        "status": "active",
    }
    view = GiveawayPreviewView(interaction.user.id, data)
    embed = build_giveaway_embed(data, interaction.guild, 0, "active")
    await interaction.response.send_message(content=giveaway_content(data, interaction.guild), embed=embed, view=view, ephemeral=True)
    view.message = await interaction.original_response()

# ============================================================
# COMANDO PARA CANCELAR GIVEAWAY
# ============================================================
@giveaway_group.command(name="cancelar", description="Cancela um Giveaway ativo")
@app_commands.describe(giveaway_id="ID do Giveaway")
async def giveaway_cancelar(interaction: discord.Interaction, giveaway_id: int):
    if not interaction.guild or not is_giveaway_admin(interaction.user):
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não possui permissão para isso.", ephemeral=True)
        return
    data = load_giveaway(giveaway_id)
    if not data or data["guild_id"] != interaction.guild.id:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Giveaway não encontrado.", ephemeral=True)
        return
    if data["status"] != "active":
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Esse Giveaway não está ativo.", ephemeral=True)
        return
    db = giveaway_db()
    cur = db.cursor()
    cur.execute("UPDATE giveaways SET status = 'cancelled' WHERE giveaway_id = ?", (giveaway_id,))
    db.commit()
    db.close()
    task = giveaway_tasks.pop(giveaway_id, None)
    if task:
        task.cancel()
    channel = interaction.guild.get_channel(data["channel_id"])
    if channel and data.get("message_id"):
        try:
            message = await channel.fetch_message(data["message_id"])
            db = giveaway_db(); cur = db.cursor(); cur.execute("SELECT COUNT(*) FROM giveaway_participants WHERE giveaway_id = ?", (giveaway_id,)); participants = cur.fetchone()[0]; db.close()
            await message.edit(embed=build_giveaway_embed(data, interaction.guild, participants, "cancelled"), view=None)
        except discord.NotFound:
            logging.warning(
                "Mensagem do Giveaway #%s não existe mais no Discord (message_id=%s).",
                giveaway_id, data["message_id"]
            )
        except discord.HTTPException as exc:
            logging.warning("Não foi possível atualizar Giveaway cancelado #%s: %s", giveaway_id, exc)
    await interaction.response.send_message(f"{EMOJI_REQUIEM} Giveaway **#{giveaway_id}** cancelado.", ephemeral=True)


# ============================================================
# SISTEMA DE TICKETS
# ============================================================
TICKET_DATABASE = "tickets.db"
TICKET_CATEGORY_ID = 1529330805707636847
TICKET_LOG_CHANNEL_ID = 1529854569721626656

TICKET_PANEL_FOOTER_TEXT = "ⓘ Requiem"
TICKET_CHANNEL_FOOTER_TEXT = " ⓘ Requiem"

TICKET_PANEL_IMAGE = (
  "https://media.discordapp.net/attachments/1541462822146277376/"
  "1542624872029757540/ChatGPT_Image_Aug_27_2026_05_00_14_PM.png"
  "?ex=6a91e8de&is=6a90975e&hm=daa468c2b8f9135f0da4a9e4979791f2d0fd7faf045720d0375ff86b5e9f4aa8"
  "&=&format=webp&quality=lossless&width=967&height=544"
)

EMOJI_FAQ_IMPORTANTE = "<:z_requiem:1539260538054508604>"

TICKET_TYPES = {
    "duvidas": {
        "value": "duvidas",
        "label": "Dúvidas",
        "select_emoji": "<:requiem:1538409316674183358>",
        "select_description": "Para perguntas, problemas ou qualquer informação relacionada ao servidor.",
        "roles": [
            1529370558427430922, 1531979955754242259, 1531979365481447574,
            1529370748303577118, 1529370672311308358, 1529317453123813547,
            1529317379677356152, 1529318511225344151, 1529054909360508958,
        ],
        "ticket_icon": "<:z_requiem:1539260648184225863>",
        "titulo": "Dúvidas",
        "corpo": (
            "↪ Explique abaixo o que você precisa saber ou o problema que está enfrentando. "
            "Quanto mais detalhes você fornecer, mais fácil será para nossa equipe ajudar."
        ),
        "intro": "Seu ticket foi criado para que você possa tirar suas dúvidas com a equipe do **Requiem**.",
        "prefixo_canal": "duvida",
    },
    "denuncias": {
        "value": "denuncias",
        "label": "Denúncias",
        "select_emoji": "<:requiem:1533006334469996546>",
        "select_description": "Para denunciar membros ou situações que estejam descumprindo as regras.",
        "roles": [1531979955754242259, 1531979365481447574, 1529370748303577118],
        "ticket_icon": "<:z_requiem:1539260591909249126>",
        "titulo": "Denúncias",
        "corpo": (
            "↪ Explique abaixo o que aconteceu e, se possível, envie provas como prints, "
            "vídeos ou outras informações que possam ajudar na análise da situação."
        ),
        "intro": "Seu ticket de **Denúncia** foi criado para que você possa informar uma situação à equipe do **Requiem**.",
        "prefixo_canal": "denuncia",
    },
    "parcerias": {
        "value": "parcerias",
        "label": "Parcerias",
        "select_emoji": "<:requiem:1533006368905101372>",
        "select_description": "Para fazer ou renovar parceria entre servidores.",
        "roles": [1529316135936196728],
        "ticket_icon": "<:z_requiem:1539260676206231583>",
        "titulo": "Parcerias",
        "corpo": (
            "↪ Explique abaixo sua proposta e envie as informações necessárias sobre o "
            "servidor, projeto ou comunidade que deseja apresentar."
        ),
        "intro": "Seu ticket de **Parceria** foi criado para que você possa apresentar sua proposta à equipe do **Requiem**.",
        "prefixo_canal": "parceria",
    },
    "patrocinios": {
        "value": "patrocinios",
        "label": "Patrocínios",
        "select_emoji": "<:requiem:1533006142307831868>",
        "select_description": "Para propostas comerciais, patrocínios ou assuntos relacionados a divulgação.",
        "roles": [1529054909360508958],
        "ticket_icon": "<:requiem:1533006344980791346>",
        "titulo": "Patrocínios",
        "corpo": (
            "↪ Explique abaixo sua proposta de patrocínio, incluindo as informações "
            "necessárias sobre sua empresa, projeto, serviço ou campanha."
        ),
        "intro": "Seu ticket de **Patrocínio** foi criado para que você possa apresentar sua proposta à equipe do **Requiem**.",
        "prefixo_canal": "patrocinio",
    },
}


def ticket_db():
    return sqlite3.connect(TICKET_DATABASE)


def setup_ticket_database():
    db = ticket_db()
    cur = db.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            ticket_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            claimed_by INTEGER,
            created_at INTEGER NOT NULL
        )
    """)
    db.commit()
    db.close()


setup_ticket_database()


def get_open_ticket_for_user(guild_id, user_id):
    db = ticket_db()
    cur = db.cursor()
    cur.execute(
        "SELECT channel_id FROM tickets WHERE guild_id = ? AND user_id = ? AND status = 'open'",
        (guild_id, user_id),
    )
    row = cur.fetchone()
    db.close()
    return row[0] if row else None


def get_ticket(channel_id):
    db = ticket_db()
    cur = db.cursor()
    cur.execute(
        "SELECT channel_id, guild_id, user_id, ticket_type, status, claimed_by, created_at "
        "FROM tickets WHERE channel_id = ?",
        (channel_id,),
    )
    row = cur.fetchone()
    db.close()
    if not row:
        return None
    keys = ["channel_id", "guild_id", "user_id", "ticket_type", "status", "claimed_by", "created_at"]
    return dict(zip(keys, row))


def create_ticket_record(channel_id, guild_id, user_id, ticket_type):
    db = ticket_db()
    cur = db.cursor()
    cur.execute(
        "INSERT INTO tickets (channel_id, guild_id, user_id, ticket_type, status, created_at) "
        "VALUES (?, ?, ?, ?, 'open', ?)",
        (channel_id, guild_id, user_id, ticket_type, now_ts()),
    )
    db.commit()
    db.close()


def set_ticket_claimed(channel_id, staff_id):
    db = ticket_db()
    cur = db.cursor()
    cur.execute("UPDATE tickets SET claimed_by = ? WHERE channel_id = ?", (staff_id, channel_id))
    db.commit()
    db.close()


def set_ticket_closed(channel_id):
    db = ticket_db()
    cur = db.cursor()
    cur.execute("UPDATE tickets SET status = 'closed' WHERE channel_id = ?", (channel_id,))
    db.commit()
    db.close()


def build_support_panel_embed(guild):
    description = (
        "⁺ ㅤㅤㅤㅤㅤ⊹ ㅤㅤㅤㅤㅤ⁺ ㅤㅤㅤㅤㅤ ⊹ㅤㅤㅤㅤㅤ⁺ ㅤㅤㅤㅤㅤㅤ\n\n"
        "# ㅤㅤㅤㅤ: ㅤㅤㅤ `  S U P O R T E  `          ◞\n\n"
        f"-# {EMOJI_ANIMATED_REQUIEM}      :     Aqui você pode entrar em contato com a equipe do "
        "servidor para receber auxílio, tirar dúvidas ou tratar de assuntos específicos;\n"
        f"-# {EMOJI_ANIMATED_REQUIEM}      :     Basta selecionar uma das opções abaixo e abrir um ticket. "
        "Um membro da equipe irá te atender assim que possível.\n\n"
        "*⁺ ㅤㅤㅤㅤㅤ↪ ㅤ Opções:*\n\n"
        "<:requiem:1538409316674183358>  **Dúvidas**\n"
        "ㅤㅤㅤㅤㅤㅤ:  Para perguntas, problemas ou qualquer informação relacionada ao servidor.\n\n"
        " <:requiem:1533006334469996546>      [**Denúncias**]"
        "(https://discordapp.com/channels/1528960946825855037/1528974980245622846)\n"
        "ㅤㅤㅤㅤㅤㅤ:  Para denunciar membros ou situações que estejam descumprindo as regras.\n\n"
        " <:requiem:1533006368905101372>       [**Parcerias**]"
        "(https://discordapp.com/channels/1528960946825855037/1529330293323071558)\n"
        "ㅤㅤㅤㅤㅤㅤ:  Para fazer ou renovar pareceria entre servidores.\n\n"
        "<:requiem:1533006142307831868>     [**Patrocínios**]"
        "(https://discordapp.com/channels/1528960946825855037/1529352023311519835)\n"
        "ㅤㅤㅤㅤㅤㅤ:  Para propostas comerciais, patrocínios ou assuntos relacionados a divulgação.\n\n"
        "⊹ ࣪ ˖ ㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤ⊹ ࣪ ˖\n\n"
        "ㅤㅤㅤㅤㅤㅤ**Selecione uma opção abaixo para abrir seu ticket.**\n"
        "-# ㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤ↪ Escolha a opção que melhor corresponde ao motivo do seu contato.\n\n"
        "⁺ ㅤㅤㅤㅤㅤ⊹ ㅤㅤㅤㅤㅤ⁺ ㅤㅤㅤㅤㅤ ⊹ ㅤㅤㅤㅤㅤ⁺"
    )
    embed = discord.Embed(description=description, color=GIVEAWAY_COLOR)
    embed.set_image(url=TICKET_PANEL_IMAGE)
    embed.set_footer(text=TICKET_PANEL_FOOTER_TEXT, icon_url=FOOTER_ICON)
    return embed


def build_faq_embed():
    description = (
        "⁺ ㅤㅤㅤㅤㅤ⊹ ㅤㅤㅤㅤㅤ⁺ ㅤㅤㅤㅤㅤ ⊹\n\n"
        "# <:z_requiem:1539260704799072296> ㅤㅤㅤ **Perguntas frequentes**\n\n"
        "↪ Respostas rápidas para as dúvidas mais comuns sobre o servidor.\n\n"
        "**𑣲. Como recebo cargos?**\n"
        "-# Os cargos podem ser obtidos através de "
        "[VIPs](https://discordapp.com/channels/1528960946825855037/1529007375804272771), "
        "[Booster](https://discordapp.com/channels/1528960946825855037/1529006925180567652), ou Eventos.\n\n"
        "**𑣲. Como participar dos sorteios?**\n"
        "-# Acesse o canal de sorteios e siga os requisitos informados em cada evento.\n\n"
        "**𑣲. Como recebo perm de mídia no chat geral?**\n"
        "-# As permissões são obtidas por "
        "[VIPs](https://discordapp.com/channels/1528960946825855037/1529007375804272771), "
        "[Booster](https://discordapp.com/channels/1528960946825855037/1529006925180567652) ou "
        "[Family](https://discordapp.com/channels/1528960946825855037/1529921802707665060).\n\n"
        "**𑣲. Como me tornar Booster?**\n"
        "-# Basta impulsionar o servidor através do Discord para receber os benefícios exclusivos.\n\n"
        "**𑣲. Onde vejo os benefícios de Booster?**\n"
        "-# Todas as vantagens estão disponíveis no canal de informações para Boosters, "
        "[clique aqui!](https://discordapp.com/channels/1528960946825855037/1529006925180567652) .\n\n"
        "**𑣲. Posso divulgar meu servidor ou redes sociais?**\n"
        "-# Não sem autorização prévia da equipe. Divulgações não autorizadas podem resultar em punições.\n\n"
        "**𑣲. Como faço uma denúncia?**\n"
        "-# Abra um ticket na categoria de denúncia e envie as informações necessárias sobre a situação.\n\n"
        "**𑣲. O que fazer se alguém estiver quebrando as regras?**\n"
        "-# Utilize a opção de denúncias e encaminhe provas para a equipe responsável.\n\n"
        "**𑣲. Como reporto bugs ou problemas do servidor?**\n"
        "-# Abra um ticket de suporte explicando o problema encontrado.\n\n"
        "**𑣲. Como participar de eventos?**\n"
        "-# Fique atento aos anúncios e avisos publicados pela equipe do servidor.\n\n"
        "**𑣲. Posso trocar meu apelido?**\n"
        "-# Caso você tenha permissão, você pode alterar contanto que não quebre regras.\n\n"
        "**𑣲. Onde encontro as regras?**\n"
        "-# As regras estão disponíveis nos canais de informações e leitura obrigatória, "
        "[clique aqui](https://discordapp.com/channels/1528960946825855037/1528974980245622846)!\n\n"
        "⊹ ࣪ ˖\n\n"
        f"-# {EMOJI_FAQ_IMPORTANTE} **Importante**\n"
        "-# ↪ Ler as regras não é opcional. Permanecer no servidor implica concordar com todas "
        "as diretrizes estabelecidas pela equipe.\n"
        "-# ↪ Caso sua dúvida não esteja listada acima, abra um ticket e nossa equipe irá ajudar você.\n\n"
        "⁺ ㅤㅤㅤㅤㅤ⊹ ㅤㅤㅤㅤㅤ⁺ ㅤㅤㅤㅤㅤ ⊹"
    )
    embed = discord.Embed(description=description, color=GIVEAWAY_COLOR)
    embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    return embed


def build_ticket_channel_embed(ticket_type, member):
    info = TICKET_TYPES[ticket_type]
    description = (
        f"# {info['ticket_icon']} **{info['titulo']}**\n\n"
        f"Olá, {member.mention}! {info['intro']}\n\n"
        f"{info['corpo']}\n\n"
        f"-# {EMOJI_REQUIEM} **Atendimento**\n"
        "-# Este ticket é privado e está disponível somente para você e para a equipe "
        "responsável pelo atendimento."
    )
    embed = discord.Embed(description=description, color=GIVEAWAY_COLOR)
    embed.set_footer(text=TICKET_CHANNEL_FOOTER_TEXT, icon_url=FOOTER_ICON)
    return embed


def build_parceria_requisitos_embed():
    description = (
        f"# {EMOJI_Z_REQUIEM} **Parcerias**\n"
        "Olá! Gostaria de fazer uma parceria com o **Requiem**?\n"
        f"### {EMOJI_Z_REQUIEM} **Confira nossos requisitos antes de solicitar uma parceria:**\n"
        f"{EMOJI_Z_REQUIEM} **Regras**\n"
        f"-# {EMOJI_ANIMATED_REQUIEM} O servidor deve seguir as Diretrizes da Comunidade do Discord.\n"
        f"-# {EMOJI_ANIMATED_REQUIEM} Não aceitamos servidores com conteúdo NSFW ou gore.\n"
        f"-# {EMOJI_ANIMATED_REQUIEM} É necessário possuir um cargo/ping destinado a parcerias.\n"
        f"-# {EMOJI_ANIMATED_REQUIEM} Não realizamos parcerias com lojas ou servidores voltados para vendas.\n"
        f"-# {EMOJI_ANIMATED_REQUIEM} Nossos divulgadores não atuarão como representantes do servidor parceiro.\n"
        f"-# {EMOJI_ANIMATED_REQUIEM} O convite não pode conter @everyone ou @here.\n"
        "⊹ ࣪ ˖ ʚɞ ⊹ ࣪ ˖\n"
        f"{EMOJI_Z_REQUIEM} **Está de acordo com os requisitos?**\n"
        "-# Clique em **\"Sigo os requisitos\"** para abrir seu ticket e enviar sua proposta.\n"
        f"{EMOJI_REQUIEM} **Leia os requisitos antes de abrir o ticket.**"
    )
    embed = discord.Embed(description=description, color=GIVEAWAY_COLOR)
    embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    return embed


def build_parceria_ticket_embed(member):
    description = (
        "# <:z_requiem:1539260676206231583> **Parceria**\n"
        f"Olá, {member.mention}! Seu ticket de parceria foi criado. ♡\n"
        "↪ Envie **somente o convite do seu servidor** neste ticket e aguarde alguém da "
        "equipe responsável pelo atendimento.\n"
        "-# <:z_requiem:1539260704799072296> **Importante**\n"
        "-# Convites contendo **@everyone** ou **@here** não serão enviados/divulgados pela nossa equipe.\n"
        "-# Antes de enviar o convite, certifique-se de remover essas marcações.\n"
        "⊹ ࣪ ˖ ʚɞ ⊹ ࣪ ˖\n"
        "<a:z_requiem:1533134533275422944> **Agora é só enviar o convite e aguardar o atendimento.**"
    )
    embed = discord.Embed(description=description, color=GIVEAWAY_COLOR)
    embed.set_footer(text=TICKET_CHANNEL_FOOTER_TEXT, icon_url=FOOTER_ICON)
    return embed


TICKET_CLAIM_DM_IMAGE = (
    "https://media.discordapp.net/attachments/1541462822146277376/"
    "1542624872029757540/ChatGPT_Image_Aug_27_2026_05_00_14_PM.png"
    "?ex=6a91e8de&is=6a90975e&hm=daa468c2b8f9135f0da4a9e4979791f2d0fd7faf045720d0375ff86b5e9f4aa8"
    "&=&format=webp&quality=lossless&width=550&height=183"
)


def ticket_mention_line(ticket_type, member):
    info = TICKET_TYPES[ticket_type]
    role_mentions = " ".join(f"<@&{role_id}>" for role_id in info["roles"])
    return f"{member.mention} {role_mentions}".strip()


def member_can_manage_ticket(member, ticket):
    if member.guild_permissions.administrator:
        return True
    if member.id == ticket["user_id"]:
        return True
    info = TICKET_TYPES.get(ticket["ticket_type"])
    if info and any(role.id in info["roles"] for role in member.roles):
        return True
    return False


def member_can_claim_ticket(member, ticket):
    if member.guild_permissions.administrator:
        return True
    info = TICKET_TYPES.get(ticket["ticket_type"])
    if info and any(role.id in info["roles"] for role in member.roles):
        return True
    return False


async def send_ticket_log(guild, titulo, ticket, extra_lines=None):
    channel = guild.get_channel(TICKET_LOG_CHANNEL_ID)
    if not channel:
        return
    member = guild.get_member(ticket["user_id"])
    nome_usuario = member.display_name if member else str(ticket["user_id"])
    descricao = (
        f"# {EMOJI_Z_REQUIEM} {titulo}\n\n"
        f"{EMOJI_REQUIEM} Usuário: {nome_usuario} (`{ticket['user_id']}`)\n"
        f"{EMOJI_REQUIEM} Tipo: {TICKET_TYPES.get(ticket['ticket_type'], {}).get('label', ticket['ticket_type'])}\n"
        f"{EMOJI_REQUIEM} Canal: `#{ticket['channel_id']}`"
    )
    if extra_lines:
        descricao += "\n" + "\n".join(extra_lines)
    embed = discord.Embed(description=descricao, color=GIVEAWAY_COLOR)
    embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    try:
        await channel.send(embed=embed)
    except discord.HTTPException as exc:
        logging.warning("Não foi possível enviar log de ticket: %s", exc)


class TicketCloseConfirmView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=30)
        self.channel_id = channel_id

    @discord.ui.button(label="Sim, fechar ticket", style=discord.ButtonStyle.secondary)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = get_ticket(self.channel_id)
        if not ticket or ticket["status"] != "open":
            await interaction.response.edit_message(content=f"{EMOJI_Z_REQUIEM} Este ticket já foi fechado.", view=None)
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"{EMOJI_REQUIEM} Fechando o ticket...", view=self)
        set_ticket_closed(self.channel_id)
        await send_ticket_log(
            interaction.guild, "Ticket fechado.", ticket,
            extra_lines=[f"{EMOJI_REQUIEM} Fechado por: {interaction.user.mention}"],
        )
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete(reason=f"Ticket fechado por {interaction.user}")
        except discord.HTTPException as exc:
            logging.warning("Não foi possível excluir canal de ticket: %s", exc)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"{EMOJI_Z_REQUIEM} Fechamento cancelado.", view=self)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Reivindicar", style=discord.ButtonStyle.secondary, custom_id="ticket_claim", emoji=EMOJI_REQUIEM_ALT)
    async def reivindicar(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = get_ticket(interaction.channel_id)
        if not ticket or ticket["status"] != "open":
            await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Este canal não é um ticket ativo.", ephemeral=True)
            return
        if not member_can_claim_ticket(interaction.user, ticket):
            await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não faz parte da equipe responsável por este ticket.", ephemeral=True)
            return
        if ticket.get("claimed_by"):
            await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Este ticket já foi reivindicado por <@{ticket['claimed_by']}>.", ephemeral=True)
            return
        set_ticket_claimed(interaction.channel_id, interaction.user.id)
        await interaction.response.send_message(f"{EMOJI_REQUIEM} Ticket reivindicado por {interaction.user.mention}.")
        await send_ticket_log(
            interaction.guild, "Ticket reivindicado.", ticket,
            extra_lines=[f"{EMOJI_REQUIEM} Reivindicado por: {interaction.user.mention}"],
        )
        dono = interaction.guild.get_member(ticket["user_id"])
        if dono:
            dm_embed = discord.Embed(
                description=(
                    f"# {EMOJI_REQUIEM} Seu ticket foi assumido!\n\n"
                    f"↪ {interaction.user.mention} assumiu seu ticket e já pode te atender.\n"
                    f"-# Acesse seu ticket aqui: {interaction.channel.mention}"
                ),
                color=GIVEAWAY_COLOR,
            )
            dm_embed.set_image(url=TICKET_CLAIM_DM_IMAGE)
            dm_embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
            try:
                await dono.send(embed=dm_embed)
            except discord.Forbidden:
                pass

    @discord.ui.button(label="Fechar", style=discord.ButtonStyle.secondary, custom_id="ticket_close", emoji=EMOJI_REQUIEM)
    async def fechar(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket = get_ticket(interaction.channel_id)
        if not ticket or ticket["status"] != "open":
            await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Este canal não é um ticket ativo.", ephemeral=True)
            return
        if not member_can_manage_ticket(interaction.user, ticket):
            await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não pode fechar este ticket.", ephemeral=True)
            return
        view = TicketCloseConfirmView(interaction.channel_id)
        await interaction.response.send_message(
            f"{EMOJI_Z_REQUIEM} Tem certeza que deseja fechar este ticket?", view=view, ephemeral=True
        )


async def create_ticket_channel(interaction: discord.Interaction, ticket_type: str):
    guild = interaction.guild
    info = TICKET_TYPES[ticket_type]

    existing_channel_id = get_open_ticket_for_user(guild.id, interaction.user.id)
    if existing_channel_id and guild.get_channel(existing_channel_id):
        await interaction.response.send_message(
            f"{EMOJI_Z_REQUIEM} Você já possui um ticket aberto em <#{existing_channel_id}>.",
            ephemeral=True,
        )
        return

    category = guild.get_channel(TICKET_CATEGORY_ID)
    if category is None:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Categoria de tickets não encontrada.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
    }
    for role_id in info["roles"]:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    channel_name = f"{info['prefixo_canal']}-{interaction.user.name}"[:90]
    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"Ticket de {info['label']} aberto por {interaction.user}",
        )
    except discord.HTTPException as exc:
        await interaction.followup.send(f"{EMOJI_Z_REQUIEM} Não foi possível criar o canal do ticket.\n```{exc}```", ephemeral=True)
        return

    create_ticket_record(channel.id, guild.id, interaction.user.id, ticket_type)

    embed = build_parceria_ticket_embed(interaction.user) if ticket_type == "parcerias" else build_ticket_channel_embed(ticket_type, interaction.user)
    try:
        await channel.send(
            content=ticket_mention_line(ticket_type, interaction.user),
            embed=embed,
            view=TicketControlView(),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
    except discord.HTTPException as exc:
        logging.warning("Erro ao enviar mensagem inicial do ticket: %s", exc)

    ticket = get_ticket(channel.id)
    await send_ticket_log(guild, f"Ticket de {info['label']} aberto.", ticket)

    await interaction.followup.send(
        f"{EMOJI_REQUIEM} Seu ticket foi criado: {channel.mention}", ephemeral=True
    )


async def checar_mencao_proibida_parceria(message: discord.Message):
    """Em tickets de parceria, remove convites com @everyone/@here e avisa só quem enviou."""
    if not message.guild:
        return
    ticket = get_ticket(message.channel.id)
    if not ticket or ticket["status"] != "open" or ticket["ticket_type"] != "parcerias":
        return
    if "@everyone" not in message.content and "@here" not in message.content:
        return
    try:
        await message.delete()
    except discord.HTTPException:
        pass
    aviso_embed = discord.Embed(
        description=(
            f"{EMOJI_Z_REQUIEM} {message.author.mention}, remova as menções **@everyone**/**@here** "
            "do convite antes de enviá-lo. Isso não é permitido."
        ),
        color=GIVEAWAY_COLOR,
    )
    aviso_embed.set_footer(text=TICKET_CHANNEL_FOOTER_TEXT, icon_url=FOOTER_ICON)
    try:
        aviso = await message.channel.send(content=message.author.mention, embed=aviso_embed)
        await asyncio.sleep(12)
        await aviso.delete()
    except discord.HTTPException:
        pass


class ParceriaRequisitosView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Sigo os requisitos", style=discord.ButtonStyle.secondary)
    async def sigo_os_requisitos(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_ticket_channel(interaction, "parcerias")

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"{EMOJI_Z_REQUIEM} Ok, o ticket não foi aberto.", embed=None, view=self)


class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=info["label"],
                value=key,
                description=info["select_description"][:100],
                emoji=info["select_emoji"],
            )
            for key, info in TICKET_TYPES.items()
        ]
        super().__init__(
            placeholder="Selecione uma opção para abrir seu ticket",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket_select_menu",
        )

    async def callback(self, interaction: discord.Interaction):
        ticket_type = self.values[0]
        if ticket_type == "parcerias":
            await interaction.response.send_message(
                embed=build_parceria_requisitos_embed(), view=ParceriaRequisitosView(), ephemeral=True
            )
            return
        await create_ticket_channel(interaction, ticket_type)


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


class FAQView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Perguntas Frequentes! 𑣲.",
        style=discord.ButtonStyle.secondary,
        custom_id="btn_faq_suporte",
        emoji="<a:z_requiem:1533134533275422944>",
    )
    async def faq_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_faq_embed(), ephemeral=True)


@bot.tree.command(name="painel-ticket", description="Envia o painel de suporte com o menu de abertura de tickets")
async def painel_ticket(interaction: discord.Interaction):
    if not interaction.guild or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Use este comando no servidor principal.", ephemeral=True)
        return
    if not any(role.id == SORTEIO_EMBED_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não possui permissão para utilizar este comando.", ephemeral=True)
        return
    await interaction.channel.send(embed=build_support_panel_embed(interaction.guild), view=TicketPanelView())
    await interaction.response.send_message(f"{EMOJI_REQUIEM} Painel de suporte enviado.", ephemeral=True)


@bot.tree.command(name="painel-faq", description="Envia o botão de Perguntas Frequentes")
async def painel_faq(interaction: discord.Interaction):
    if not interaction.guild or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Use este comando no servidor principal.", ephemeral=True)
        return
    if not any(role.id == SORTEIO_EMBED_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não possui permissão para utilizar este comando.", ephemeral=True)
        return
    await interaction.channel.send(view=FAQView())
    await interaction.response.send_message(f"{EMOJI_REQUIEM} Botão de FAQ enviado.", ephemeral=True)

# ============================================================
# READY
# ============================================================
@bot.event
async def on_ready():
    global bot_ready_initialized
    logging.info("Bot conectado como %s", bot.user)
    logging.info("[GATEWAY] Members Intent no código: %s", intents.members)
    logging.info("[BOAS-VINDAS] Chat geral configurado: %s", GENERAL_CHAT_CHANNEL_ID)
    if bot_ready_initialized:
        return
    bot_ready_initialized = True
    try:
        await bot.tree.sync(guild=discord.Object(id=MAIN_GUILD_ID))
        await bot.tree.sync()
        logging.info("Comandos slash sincronizados.")
    except Exception as exc:
        logging.error("Erro ao sincronizar comandos: %s", exc)
    try:
        await restore_giveaways()
    except Exception as exc:
        logging.error("Erro ao restaurar Giveaways: %s", exc)
    bot.add_view(TicketPanelView())
    bot.add_view(FAQView())
    bot.add_view(TicketControlView())
    if not verificar_bump.is_running():
        verificar_bump.start()
    if not verificar_horarios_iguais.is_running():
        verificar_horarios_iguais.start()
    logging.info("O seu Bot está ligado!")

# ============================================================
# BOOST
# ============================================================
@bot.event
async def on_member_update(before, after):
    if not before.guild:
        return
    guild = after.guild
    onebooster = guild.premium_subscriber_role
    role_id = get_role_id()
    if not onebooster or not role_id:
        return
    doublebooster = guild.get_role(role_id)
    if onebooster in before.roles and onebooster not in after.roles:
        if doublebooster:
            try:
                await after.remove_roles(doublebooster, reason="Membro deixou de ser Booster.")
            except discord.Forbidden:
                logging.warning("Sem permissão para remover Double Booster.")
        if guild.system_channel:
            try:
                await guild.system_channel.send(f"{after.mention} não é mais Booster")
            except discord.HTTPException:
                pass
        try:
            await changeBoost(after, 0)
        except Exception as exc:
            logging.error("Erro ao atualizar boost: %s", exc)

# ============================================================
# BUMP
# ============================================================
@tasks.loop(minutes=1)
async def verificar_bump():
    global aviso_bump_enviado
    CANAL_BUMP_ID = 1539448403715428463
    CARGO_BUMP_ID = 1529744653585485874
    if ultimo_bump_tempo is None or aviso_bump_enviado:
        return
    try:
        if datetime.now(timezone.utc) - ultimo_bump_tempo.astimezone(timezone.utc) >= timedelta(minutes=120):
            for guild in bot.guilds:
                channel = guild.get_channel(CANAL_BUMP_ID)
                if not channel:
                    continue
                embed = discord.Embed(description=f"# Hora do Bump! {EMOJI_REQUIEM}\n{EMOJI_ANIMATED_REQUIEM} O tempo de espera acabou, vamos divulgar o servidor.\n\n-# Utilize o comando `/bump` para ajudar a comunidade a crescer.", color=GIVEAWAY_COLOR)
                if guild.icon:
                    embed.set_thumbnail(url=guild.icon.url)
                embed.set_image(url=GIVEAWAY_FOOTER_IMAGE)
                embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
                await channel.send(content=f"<@&{CARGO_BUMP_ID}>", embed=embed)
            aviso_bump_enviado = True
    except Exception as exc:
        logging.error("Erro BUMP: %s", exc)

# ============================================================
# HORÁRIOS IGUAIS
# ============================================================
@tasks.loop(seconds=1)
async def verificar_horarios_iguais():
    global ultima_mensagem_horario

    # Horário de Brasília. A verificação ocorre a cada segundo para garantir
    # que o bot não perca horários como 11:11, 22:22, 00:00 etc.
    try:
        agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
    except Exception:
        agora = datetime.now(timezone(timedelta(hours=-3)))

    horario = agora.strftime("%H:%M")
    hora, minuto = horario.split(":")

    # Só envia quando hora e minuto são iguais.
    if hora != minuto:
        return

    # Impede duplicação durante o mesmo minuto.
    if ultima_mensagem_horario == horario:
        return

    guild = bot.get_guild(MAIN_GUILD_ID)
    if guild is None:
        logging.warning("[HORAS IGUAIS] Servidor principal não encontrado.")
        return

    channel = guild.get_channel(GENERAL_CHAT_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(GENERAL_CHAT_CHANNEL_ID)
        except discord.NotFound:
            logging.error("[HORAS IGUAIS] O canal geral %s não existe.", GENERAL_CHAT_CHANNEL_ID)
            return
        except discord.Forbidden:
            logging.error("[HORAS IGUAIS] Sem permissão para acessar o canal %s.", GENERAL_CHAT_CHANNEL_ID)
            return
        except discord.HTTPException as exc:
            logging.error("[HORAS IGUAIS] Erro ao buscar o canal %s: %s", GENERAL_CHAT_CHANNEL_ID, exc)
            return

    try:
        embed = discord.Embed(
            description=f"### {EMOJI_ANIMATED_REQUIEM} {horario}",
            color=GIVEAWAY_COLOR,
        )
        embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)

        await channel.send(
            embed=embed,
            delete_after=60,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        ultima_mensagem_horario = horario
        logging.info("[HORAS IGUAIS] %s enviado no chat geral %s.", horario, GENERAL_CHAT_CHANNEL_ID)

    except discord.Forbidden as exc:
        logging.error("[HORAS IGUAIS] Sem permissão para enviar mensagem no canal %s: %s", GENERAL_CHAT_CHANNEL_ID, exc)
    except discord.HTTPException as exc:
        logging.error("[HORAS IGUAIS] Discord recusou o envio no canal %s: %s", GENERAL_CHAT_CHANNEL_ID, exc)
    except Exception as exc:
        logging.exception("[HORAS IGUAIS] Erro inesperado: %s", exc)


@verificar_horarios_iguais.before_loop
async def antes_verificar_horarios_iguais():
    await bot.wait_until_ready()

# ============================================================
# BOAS-VINDAS / MENSAGENS
# ============================================================
@bot.event
async def on_member_join(member):
    # O sistema de boas-vindas funciona somente no servidor principal.
    if member.guild.id != MAIN_GUILD_ID:
        return

    logging.info(
        "[BOAS-VINDAS] Novo membro detectado: %s | ID: %s | Guild: %s",
        member,
        member.id,
        member.guild.id,
    )

    canal = member.guild.get_channel(GENERAL_CHAT_CHANNEL_ID)

    # O canal pode não estar no cache; nesse caso buscamos diretamente no Discord.
    if canal is None:
        try:
            canal = await bot.fetch_channel(GENERAL_CHAT_CHANNEL_ID)
        except discord.NotFound:
            logging.error("[BOAS-VINDAS] O canal geral %s não existe.", GENERAL_CHAT_CHANNEL_ID)
            return
        except discord.Forbidden:
            logging.error("[BOAS-VINDAS] Sem permissão para acessar o canal geral %s.", GENERAL_CHAT_CHANNEL_ID)
            return
        except discord.HTTPException as exc:
            logging.error("[BOAS-VINDAS] Erro ao buscar o canal geral %s: %s", GENERAL_CHAT_CHANNEL_ID, exc)
            return

    # Garante que o destino realmente aceita mensagens.
    if not hasattr(canal, "send"):
        logging.error("[BOAS-VINDAS] O ID %s não corresponde a um canal que aceita mensagens.", GENERAL_CHAT_CHANNEL_ID)
        return

    try:
        embed = discord.Embed(
            description=(
                f"# Boas vindas ao Requiem! {EMOJI_REQUIEM}\n"
                f"{EMOJI_ANIMATED_REQUIEM} Espero que goste do servidor.\n\n"
                f"-# Veja o canal <#1528974980245622846> para ficar por dentro das regras "
                f"e faça parte da família <#1529921802707665060> {EMOJI_REQUIEM_ALT}."
            ),
            color=GIVEAWAY_COLOR,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_image(url=GIVEAWAY_FOOTER_IMAGE)
        embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)

        await canal.send(
            content=member.mention,
            embed=embed,
            delete_after=120,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        logging.info(
            "[BOAS-VINDAS] Mensagem enviada para %s no chat geral %s.",
            member,
            GENERAL_CHAT_CHANNEL_ID,
        )

    except discord.Forbidden as exc:
        logging.error(
            "[BOAS-VINDAS] Sem permissão para enviar mensagens/embeds no canal %s: %s",
            GENERAL_CHAT_CHANNEL_ID,
            exc,
        )
    except discord.HTTPException as exc:
        logging.error("[BOAS-VINDAS] Erro da API do Discord: %s", exc)
    except Exception as exc:
        logging.exception("[BOAS-VINDAS] Erro inesperado: %s", exc)


@bot.event
async def on_message(message):
    global ultimo_bump_tempo, aviso_bump_enviado
    if message.guild is None:
        await bot.process_commands(message)
        return
    CANAL_BUMP_ID = 1539448403715428463
    DISBOARD_ID = 302050872383242240
    if message.channel.id == CANAL_BUMP_ID and message.author.id == DISBOARD_ID:
        ultimo_bump_tempo = message.created_at
        aviso_bump_enviado = False
    if not message.author.bot and message.guild.system_channel and message.channel.id == message.guild.system_channel.id:
        boost_types = [discord.MessageType.premium_guild_subscription, discord.MessageType.premium_guild_tier_1, discord.MessageType.premium_guild_tier_2, discord.MessageType.premium_guild_tier_3]
        if message.type in boost_types:
            try:
                user = message.author
                boosts = await addBoost(user)
                role_id = get_role_id()
                if boosts >= 2:
                    role = message.guild.get_role(role_id) if role_id else None
                    if role:
                        try:
                            await user.add_roles(role, reason="Usuário atingiu 2x Boost.")
                        except discord.Forbidden:
                            pass
                    await message.channel.send(f"{user.mention} virou 2x booster {EMOJI_REQUIEM_ALT}!")
                else:
                    await message.channel.send(f"{user.mention} virou 1x booster {EMOJI_REQUIEM}!")
            except Exception as exc:
                logging.error("Erro boost: %s", exc)
    if not message.author.bot:
        await checar_mencao_proibida_parceria(message)
    await update_giveaway_message_counter(message)
    await bot.process_commands(message)

# ============================================================
# SYNC / CONFIGURAÇÃO BOOST
# ============================================================
@bot.command()
@commands.is_owner()
async def sync(ctx, guild=None):
    try:
        synced = await bot.tree.sync() if guild is None else await bot.tree.sync(guild=discord.Object(id=int(guild)))
        await ctx.send(f"{EMOJI_REQUIEM} {len(synced)} comandos sincronizados.")
    except Exception as exc:
        await ctx.send(f"{EMOJI_Z_REQUIEM} Erro ao sincronizar:\n```{exc}```")


@bot.tree.command(name="configurar", description="Configure seu bot de impulsos")
@app_commands.default_permissions(administrator=True)
async def configurar(interaction):
    await interaction.response.send_message("Carregando...", ephemeral=True)
    try:
        role = await interaction.guild.create_role(name="Double Booster")
        set_role_id(role.id)
        overwrites = {interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False)}
        channel = await interaction.guild.create_text_channel(name="notificações-boosts", overwrites=overwrites)
        await interaction.guild.edit(system_channel=channel, system_channel_flags=discord.SystemChannelFlags(premium_subscriptions=True))
        for member in interaction.guild.members:
            if member.premium_since is not None:
                await changeBoost(member, 1)
    except Exception as exc:
        logging.error("Erro configurar boost: %s", exc)
        await interaction.edit_original_response(content=f"{EMOJI_Z_REQUIEM} Não foi possível finalizar.\n```{exc}```")
        return
    await interaction.edit_original_response(content=f"{EMOJI_REQUIEM} Operação concluída.\n\nCargo: {role.mention}\nCanal: {channel.mention}")

# ============================================================
# MODERAÇÃO
# ============================================================
TITULOS_LOG = {
    "ban": f"{EMOJI_Z_REQUIEM} Banimento aplicado.",
    "kick": f"{EMOJI_Z_REQUIEM} Expulsão aplicada.",
    "mute": f"{EMOJI_Z_REQUIEM} Mute aplicado.",
    "warn": f"{EMOJI_Z_REQUIEM} Warn aplicado.",
    "unban": f"{EMOJI_Z_REQUIEM} Desbanimento aplicado.",
    "unmute": f"{EMOJI_Z_REQUIEM} Desmute aplicado.",
    "hackban": f"{EMOJI_Z_REQUIEM} Banimento aplicado.",
}
AUTHORIZED_ROLES = [
    1529370558427430922, 1531979955754242259, 1531979365481447574,
    1529370748303577118, 1529370672311308358, 1529317379677356152,
    1529318511225344151, 1529341829332471848,
]


def has_mod_role():
    async def predicate(ctx):
        if await ctx.bot.is_owner(ctx.author) or ctx.author.guild_permissions.administrator:
            return True
        return any(role.id in AUTHORIZED_ROLES for role in ctx.author.roles)
    return commands.check(predicate)


def member_has_mod_role(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(role.id in AUTHORIZED_ROLES for role in member.roles)


async def executar_acao_punicao(guild, action, target_user, reason, duration_td=None):
    """Executa a ação de moderação no Discord. Lança exceção em caso de falha."""
    if action == "mute":
        await target_user.timeout(duration_td, reason=reason)
    elif action == "unmute":
        await target_user.timeout(None, reason=reason)
    elif action in ("ban", "hackban"):
        await guild.ban(target_user, reason=reason)
    elif action == "unban":
        await guild.unban(target_user, reason=reason)
    elif action == "kick":
        await target_user.kick(reason=reason)


async def registrar_log_punicao(guild, action, target_user, author, motivo, duration_str=None):
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        return
    motivo_final = motivo + (f" (Tempo: {duration_str})" if duration_str else "")
    log = discord.Embed(
        description=(
            f"# {TITULOS_LOG[action]}\n\n"
            f"Membro:\n{EMOJI_REQUIEM} {target_user.name}\n{EMOJI_REQUIEM} {target_user.id}\n\n"
            f"Autor:\n{EMOJI_REQUIEM} {author.name}\n{EMOJI_REQUIEM} {author.id}\n\n"
            f"Motivo:\n{EMOJI_REQUIEM} {motivo_final}"
        ),
        color=GIVEAWAY_COLOR,
    )
    if getattr(target_user, "display_avatar", None):
        log.set_thumbnail(url=target_user.display_avatar.url)
    log.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    try:
        await log_channel.send(embed=log)
    except discord.HTTPException:
        pass


class ModConfirmView(discord.ui.View):
    def __init__(self, ctx, action, target_user, reason, duration_td=None, duration_str=None):
        super().__init__(timeout=60)
        self.ctx = ctx; self.action = action; self.target_user = target_user; self.reason = reason
        self.duration_td = duration_td; self.duration_str = duration_str; self.message = None

    @discord.ui.button(label="Executar", style=discord.ButtonStyle.secondary)
    async def confirm_btn(self, interaction, button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não pode usar este botão.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await executar_acao_punicao(self.ctx.guild, self.action, self.target_user, self.reason, self.duration_td)
        except discord.Forbidden:
            await interaction.followup.send(f"{EMOJI_Z_REQUIEM} Não tenho permissão para executar esta ação.", ephemeral=True); return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"{EMOJI_Z_REQUIEM} O Discord recusou a ação:\n```{exc}```", ephemeral=True); return
        except Exception as exc:
            await interaction.followup.send(f"{EMOJI_Z_REQUIEM} Erro:\n```{exc}```", ephemeral=True); return

        for child in self.children: child.disabled = True
        embed = discord.Embed(description=f"### {EMOJI_REQUIEM} Usuário {self.target_user.name} punido.\n-# Efetuado por {self.ctx.author.name}. Mais informações em <#{LOG_CHANNEL_ID}>.", color=GIVEAWAY_COLOR)
        embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
        if self.message:
            try: await self.message.edit(embed=embed, view=self)
            except discord.HTTPException: pass
        await registrar_log_punicao(self.ctx.guild, self.action, self.target_user, self.ctx.author, self.reason, self.duration_str)

    @discord.ui.button(label="Desistir", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction, button):
        if interaction.user.id != self.ctx.author.id: return
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content=f"{EMOJI_Z_REQUIEM} Ação cancelada.", embed=None, view=self)


async def gerenciar_punicao(ctx, action, target_user, reason, needs_time=False):
    duration_td = None; duration_str = None
    if needs_time:
        first = reason.split(" ")[0] if reason != "Sem motivos definidos" else ""
        parsed = parse_tempo(first)
        if parsed:
            duration_td = parsed; duration_str = first; reason = reason.replace(first, "", 1).strip() or "Sem motivos definidos"
        else:
            embed = discord.Embed(description=f"### {EMOJI_Z_REQUIEM} Quanto tempo de punição?", color=GIVEAWAY_COLOR); embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
            ask = await ctx.send(content=ctx.author.mention, embed=embed)
            def check(m): return m.author == ctx.author and m.channel == ctx.channel
            try:
                answer = await bot.wait_for("message", timeout=60, check=check)
                duration_str = answer.content.strip(); duration_td = parse_tempo(duration_str)
                try: await answer.delete(); await ask.delete()
                except discord.HTTPException: pass
                if not duration_td:
                    await ctx.send(f"{EMOJI_Z_REQUIEM} Formato inválido. Use `1d`, `1min`, `1sem`."); return
            except asyncio.TimeoutError:
                await ask.edit(content=f"{EMOJI_Z_REQUIEM} Tempo esgotado.", embed=None); return
    embed = discord.Embed(description=f"### {EMOJI_REQUIEM} Prosseguir com esta ação?\n{ctx.author.name} irá {action} {target_user.name} por {reason}", color=GIVEAWAY_COLOR); embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    view = ModConfirmView(ctx, action, target_user, reason, duration_td, duration_str)
    view.message = await ctx.send(embed=embed, view=view)


@bot.command()
@has_mod_role()
async def mute(ctx, member: discord.Member, *, motivo="Sem motivos definidos"): await gerenciar_punicao(ctx, "mute", member, motivo, True)

@bot.command()
@has_mod_role()
async def unmute(ctx, member: discord.Member, *, motivo="Sem motivos definidos"): await gerenciar_punicao(ctx, "unmute", member, motivo)

@bot.command()
@has_mod_role()
async def ban(ctx, member: discord.Member, *, motivo="Sem motivos definidos"): await gerenciar_punicao(ctx, "ban", member, motivo)

@bot.command()
@has_mod_role()
async def unban(ctx, user_id: int, *, motivo="Sem motivos definidos"):
    try: await gerenciar_punicao(ctx, "unban", await bot.fetch_user(user_id), motivo)
    except discord.NotFound: await ctx.send(f"{EMOJI_Z_REQUIEM} Usuário não encontrado.")

@bot.command()
@has_mod_role()
async def hackban(ctx, user_id: int, *, motivo="Sem motivos definidos"):
    try: await gerenciar_punicao(ctx, "hackban", await bot.fetch_user(user_id), motivo)
    except discord.NotFound: await ctx.send(f"{EMOJI_Z_REQUIEM} Usuário não encontrado.")

@bot.command()
@has_mod_role()
async def kick(ctx, member: discord.Member, *, motivo="Sem motivos definidos"): await gerenciar_punicao(ctx, "kick", member, motivo)

@bot.command()
@has_mod_role()
async def warn(ctx, member: discord.Member, *, motivo="Sem motivos definidos"): await gerenciar_punicao(ctx, "warn", member, motivo)


# ------------------------------------------------------------
# VERSÃO EM SLASH (/) DA MODERAÇÃO
# ------------------------------------------------------------
class ModConfirmViewSlash(discord.ui.View):
    def __init__(self, author, guild, action, target_user, reason, duration_td=None, duration_str=None):
        super().__init__(timeout=60)
        self.author = author; self.guild = guild; self.action = action
        self.target_user = target_user; self.reason = reason
        self.duration_td = duration_td; self.duration_str = duration_str

    @discord.ui.button(label="Executar", style=discord.ButtonStyle.secondary)
    async def confirm_btn(self, interaction, button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não pode usar este botão.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await executar_acao_punicao(self.guild, self.action, self.target_user, self.reason, self.duration_td)
        except discord.Forbidden:
            await interaction.followup.send(f"{EMOJI_Z_REQUIEM} Não tenho permissão para executar esta ação.", ephemeral=True); return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"{EMOJI_Z_REQUIEM} O Discord recusou a ação:\n```{exc}```", ephemeral=True); return
        except Exception as exc:
            await interaction.followup.send(f"{EMOJI_Z_REQUIEM} Erro:\n```{exc}```", ephemeral=True); return

        for child in self.children: child.disabled = True
        embed = discord.Embed(description=f"### {EMOJI_REQUIEM} Usuário {self.target_user.name} punido.\n-# Efetuado por {self.author.name}. Mais informações em <#{LOG_CHANNEL_ID}>.", color=GIVEAWAY_COLOR)
        embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except discord.HTTPException:
            pass
        await registrar_log_punicao(self.guild, self.action, self.target_user, self.author, self.reason, self.duration_str)

    @discord.ui.button(label="Desistir", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction, button):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não pode usar este botão.", ephemeral=True)
            return
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(content=f"{EMOJI_Z_REQUIEM} Ação cancelada.", embed=None, view=self)


async def gerenciar_punicao_slash(interaction, action, target_user, reason, duration_td=None, duration_str=None):
    embed = discord.Embed(
        description=f"### {EMOJI_REQUIEM} Prosseguir com esta ação?\n{interaction.user.name} irá {action} {target_user.name} por {reason}",
        color=GIVEAWAY_COLOR,
    )
    embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    view = ModConfirmViewSlash(interaction.user, interaction.guild, action, target_user, reason, duration_td, duration_str)
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="ban", description="Bane um membro do servidor")
@app_commands.describe(usuario="Membro a ser banido", motivo="Motivo da punição")
async def slash_ban(interaction: discord.Interaction, usuario: discord.Member, motivo: str = "Sem motivos definidos"):
    if not member_has_mod_role(interaction.user):
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não possui permissão para utilizar este comando.", ephemeral=True); return
    await gerenciar_punicao_slash(interaction, "ban", usuario, motivo)


@bot.tree.command(name="kick", description="Expulsa um membro do servidor")
@app_commands.describe(usuario="Membro a ser expulso", motivo="Motivo da punição")
async def slash_kick(interaction: discord.Interaction, usuario: discord.Member, motivo: str = "Sem motivos definidos"):
    if not member_has_mod_role(interaction.user):
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não possui permissão para utilizar este comando.", ephemeral=True); return
    await gerenciar_punicao_slash(interaction, "kick", usuario, motivo)


@bot.tree.command(name="warn", description="Aplica um warn em um membro")
@app_commands.describe(usuario="Membro a ser advertido", motivo="Motivo da punição")
async def slash_warn(interaction: discord.Interaction, usuario: discord.Member, motivo: str = "Sem motivos definidos"):
    if not member_has_mod_role(interaction.user):
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não possui permissão para utilizar este comando.", ephemeral=True); return
    await gerenciar_punicao_slash(interaction, "warn", usuario, motivo)


@bot.tree.command(name="mute", description="Silencia (timeout) um membro por um tempo determinado")
@app_commands.describe(usuario="Membro a ser silenciado", tempo="Ex.: 10min, 1h, 1d, 1sem", motivo="Motivo da punição")
async def slash_mute(interaction: discord.Interaction, usuario: discord.Member, tempo: str, motivo: str = "Sem motivos definidos"):
    if not member_has_mod_role(interaction.user):
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não possui permissão para utilizar este comando.", ephemeral=True); return
    duration_td = parse_tempo(tempo)
    if not duration_td:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Tempo inválido. Use, por exemplo, `10min`, `1h`, `1d` ou `1sem`.", ephemeral=True); return
    await gerenciar_punicao_slash(interaction, "mute", usuario, motivo, duration_td, tempo)


@bot.tree.command(name="unmute", description="Remove o silenciamento (timeout) de um membro")
@app_commands.describe(usuario="Membro a ser dessilenciado", motivo="Motivo")
async def slash_unmute(interaction: discord.Interaction, usuario: discord.Member, motivo: str = "Sem motivos definidos"):
    if not member_has_mod_role(interaction.user):
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não possui permissão para utilizar este comando.", ephemeral=True); return
    await gerenciar_punicao_slash(interaction, "unmute", usuario, motivo)


@bot.tree.command(name="unban", description="Desbane um usuário pelo ID")
@app_commands.describe(usuario_id="ID do usuário a ser desbanido", motivo="Motivo")
async def slash_unban(interaction: discord.Interaction, usuario_id: str, motivo: str = "Sem motivos definidos"):
    if not member_has_mod_role(interaction.user):
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não possui permissão para utilizar este comando.", ephemeral=True); return
    if not usuario_id.isdigit():
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} O ID precisa ser numérico.", ephemeral=True); return
    try:
        user = await bot.fetch_user(int(usuario_id))
    except discord.NotFound:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Usuário não encontrado.", ephemeral=True); return
    await gerenciar_punicao_slash(interaction, "unban", user, motivo)


@bot.tree.command(name="hackban", description="Bane um usuário pelo ID mesmo que ele não esteja no servidor")
@app_commands.describe(usuario_id="ID do usuário a ser banido", motivo="Motivo")
async def slash_hackban(interaction: discord.Interaction, usuario_id: str, motivo: str = "Sem motivos definidos"):
    if not member_has_mod_role(interaction.user):
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Você não possui permissão para utilizar este comando.", ephemeral=True); return
    if not usuario_id.isdigit():
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} O ID precisa ser numérico.", ephemeral=True); return
    try:
        user = await bot.fetch_user(int(usuario_id))
    except discord.NotFound:
        await interaction.response.send_message(f"{EMOJI_Z_REQUIEM} Usuário não encontrado.", ephemeral=True); return
    await gerenciar_punicao_slash(interaction, "hackban", user, motivo)

# ============================================================
# TESTES
# ============================================================
@bot.command()
@commands.is_owner()
async def testhorario(ctx):
    channel = bot.get_channel(GENERAL_CHAT_CHANNEL_ID)
    if not channel: await ctx.send(f"{EMOJI_Z_REQUIEM} Canal não encontrado."); return
    embed = discord.Embed(description=f"### {EMOJI_ANIMATED_REQUIEM} 12:12", color=GIVEAWAY_COLOR); embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    await channel.send(embed=embed); await ctx.send(f"{EMOJI_REQUIEM} Teste enviado!", delete_after=5)

@bot.command()
@commands.is_owner()
async def testbump(ctx):
    try: await verificar_bump(); await ctx.send(f"{EMOJI_REQUIEM} Verificação executada.", delete_after=5)
    except Exception as exc: await ctx.send(f"{EMOJI_Z_REQUIEM} Erro:\n```{exc}```")

# ============================================================
# ERROS / EXECUÇÃO
# ============================================================
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound): return
    if isinstance(error, commands.MissingRequiredArgument): await ctx.send(f"{EMOJI_Z_REQUIEM} Está faltando um argumento nesse comando."); return
    if isinstance(error, commands.BadArgument): await ctx.send(f"{EMOJI_Z_REQUIEM} Um dos argumentos informados é inválido."); return
    if isinstance(error, commands.CheckFailure): await ctx.send(f"{EMOJI_Z_REQUIEM} Você não possui permissão para utilizar este comando."); return
    logging.error("Erro no comando %s: %s", ctx.command, error)


async def main():
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())