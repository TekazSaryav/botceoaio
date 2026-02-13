import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import json
import os
from datetime import datetime, timedelta
import random
import io
from dotenv import load_dotenv
import re
from deep_translator import GoogleTranslator

load_dotenv()

# Configuration du bot
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='/', intents=intents)

# Stockage en mémoire des données
guild_data = {}
giveaways = {}
warnings = {}
sticky_messages = {}
temp_voice_channels = set()
voice_temp_rooms = {}
user_cooldowns = {}
free_key_users = {}
# NOUVEAU : Suivi temps réel de l'activité des tickets
ticket_activity_tracker = {}  # {guild_id: {channel_id: {'last_activity': datetime, 'creator_id': int, 'warning_sent': bool, 'warning_message_id': int}}}

def get_guild_data(guild_id):
    """Obtenir les données d'une guilde"""
    if guild_id not in guild_data:
        guild_data[guild_id] = {
            'config': {
                'logs_channel': None,
                'autorole': None,
                'allowed_roles': [],
                'automod': False,
                'antilink': {'status': False, 'action': 'warn'},
                'antispam': {'status': False, 'action': 'warn'},
                'antiraid': {'status': False, 'action': 'ban'},
                'badword_action': 'warn',
                'whitelist_domains': ['youtube.com', 'discord.com'],
                'badwords': [],
                'welcome_channel': None,
                'welcome_message': 'Bienvenue {user} sur notre serveur !',
                'ticket_category': None,
                'ticket_roles': [],
                'ticket_logs_channel': None,
                'key_cooldown': 60,
                'key_roles': [],
                'vouch_config': {
                    'title': 'Avis Client',
                    'color': '#a30174',
                    'footer': 'Système de Vouch',
                    'thumbnail': True
                },
                'ticket_embed': {
                    'title': '🎫 Système de Tickets',
                    'description': 'Sélectionnez une catégorie pour ouvrir un ticket:',
                    'color': '#a30174',
                    'image_url': None,
                    'thumbnail_url': None
                },
                'freekey_embed': {
                    'title': '🆓 Free Keys',
                    'description': 'Récupérez votre clé gratuite\n\nUne clé par utilisateur',
                    'color': '#00ff00',
                    'image_url': None,
                    'button_label': 'Récupérer Free Key'
                },
                'key_embed': {
                    'title': '🔑 Clés Promoteur',
                    'description': 'Récupérez vos clés promoteur',
                    'color': '#0099ff',
                    'image_url': None,
                    'button_label': 'Récupérer Clé'
                },
                # NOUVEAU : Configuration système d'inactivité
                'inactivity_config': {
                    'enabled': False,  # Désactivé par défaut
                    'delay_hours': 24,  # Délai avant premier avertissement
                    'final_close_hours': 48,  # Fermeture auto après 48h total
                    'notify_staff': True,  # Notifier le staff
                    'embed': {
                        'title': '⏰ Ticket Inactif',
                        'description': 'Ce ticket est inactif depuis **{hours}h**.\n\n{mention}, souhaitez-vous :\n• Le garder ouvert 24h de plus ?\n• Le fermer définitivement ?\n\n⚠️ **Fermeture automatique dans 24h** si pas de réponse.',
                        'color': '#ff9900',
                        'image_url': None,
                        'button_keep': '🔄 Garder Ouvert',
                        'button_close': '🔒 Fermer le Ticket'
                    }
                },
                'voctemp': {
                    'source_channel_id': None
                }
            },
            'keys': [],
            'free_keys': [],
            'vouch_count': 0,
            'ticket_counter': 0,
            'ticket_categories': {
                'support': {'name': 'Support', 'description': 'Support technique', 'emoji': '🛠️'},
                'bug': {'name': 'Bug Report', 'description': 'Signaler un bug', 'emoji': '🐛'},
                'other': {'name': 'Autre', 'description': 'Autres demandes', 'emoji': '❓'}
            },
            # NOUVEAU : Suivi d'activité des tickets
            'ticket_activity': {}
        }
    return guild_data[guild_id]

async def check_permissions(interaction: discord.Interaction) -> bool:
    """Vérifier les permissions et répondre si refusé"""
    if not is_admin_or_authorized(interaction):
        await interaction.response.send_message(
            "❌ **Permissions insuffisantes!**\n"
            "Cette commande est réservée aux administrateurs et aux rôles autorisés.\n"
            "Utilisez `/setrole` pour autoriser des rôles.",
            ephemeral=True
        )
        return False
    return True

def is_admin_or_authorized(interaction: discord.Interaction) -> bool:
    """Vérifier si l'utilisateur est admin ou a un rôle autorisé"""
    # Si admin, toujours autorisé
    if interaction.user.guild_permissions.administrator:
        return True
    
    # Vérifier les rôles autorisés
    data = get_guild_data(interaction.guild.id)
    allowed_roles = data['config']['allowed_roles']
    
    user_roles = [role.id for role in interaction.user.roles]
    return any(role_id in user_roles for role_id in allowed_roles)

def update_ticket_activity(guild_id, channel_id, creator_id):
    """Mettre à jour l'activité d'un ticket"""
    if guild_id not in ticket_activity_tracker:
        ticket_activity_tracker[guild_id] = {}
    
    ticket_activity_tracker[guild_id][channel_id] = {
        'last_activity': datetime.now(),
        'creator_id': creator_id,
        'warning_sent': False,
        'warning_message_id': None,
        'extensions': 0  # Nombre de fois que le ticket a été gardé ouvert
    }
    print(f"[INACTIVITY] Activité mise à jour pour ticket {channel_id}")

def get_ticket_inactivity_hours(guild_id, channel_id):
    """Obtenir le nombre d'heures d'inactivité d'un ticket"""
    if guild_id not in ticket_activity_tracker:
        return 0
    if channel_id not in ticket_activity_tracker[guild_id]:
        return 0
    
    last_activity = ticket_activity_tracker[guild_id][channel_id]['last_activity']
    delta = datetime.now() - last_activity
    hours = delta.total_seconds() / 3600
    return round(hours, 1)

def remove_ticket_from_tracker(guild_id, channel_id):
    """Retirer un ticket du suivi d'activité"""
    if guild_id in ticket_activity_tracker:
        if channel_id in ticket_activity_tracker[guild_id]:
            del ticket_activity_tracker[guild_id][channel_id]
            print(f"[INACTIVITY] Ticket {channel_id} retiré du suivi")

def create_key_embed(guild_id):
    """Créer l'embed des keys promoteur avec la configuration sauvegardée"""
    data = get_guild_data(guild_id)
    embed_config = data['config']['key_embed']
    
    try:
        color_value = int(embed_config['color'].replace('#', ''), 16)
    except:
        color_value = 0x0099ff
    
    # Ajouter les informations de stock et cooldown
    stock_text = f"\n\n**Clés disponibles:** {len(data['keys'])}\n**Cooldown:** {data['config']['key_cooldown']} minutes"
    description = embed_config['description'] + stock_text
    
    embed = discord.Embed(
        title=embed_config['title'],
        description=description,
        color=color_value
    )
    
    if embed_config.get('image_url'):
        embed.set_image(url=embed_config['image_url'])
    
    return embed

def create_freekey_embed(guild_id):
    """Créer l'embed des free keys avec la configuration sauvegardée"""
    data = get_guild_data(guild_id)
    embed_config = data['config']['freekey_embed']
    
    try:
        color_value = int(embed_config['color'].replace('#', ''), 16)
    except:
        color_value = 0x00ff00
    
    # Ajouter le nombre de clés disponibles dans la description
    stock_text = f"\n\n**Clés disponibles:** {len(data['free_keys'])}"
    description = embed_config['description'] + stock_text
    
    embed = discord.Embed(
        title=embed_config['title'],
        description=description,
        color=color_value
    )
    
    if embed_config.get('image_url'):
        embed.set_image(url=embed_config['image_url'])
    
    return embed

def clean_category_name(category_name):
    """Nettoyer le nom de catégorie pour l'utiliser dans le nom du salon"""
    clean_name = re.sub(r'[^\w\s-]', '', category_name).strip()
    clean_name = re.sub(r'\s+', '-', clean_name).lower()
    return clean_name[:15]  # Limiter à 15 caractères

def clean_username(username):
    """Nettoyer le nom d'utilisateur pour l'utiliser dans le nom du salon"""
    clean_name = re.sub(r'[^\w\s-]', '', username).strip()
    clean_name = re.sub(r'\s+', '-', clean_name).lower()
    return clean_name[:10]  # Limiter à 10 caractères

def get_next_ticket_number(guild_id):
    """Obtenir le prochain numéro de ticket"""
    data = get_guild_data(guild_id)
    data['ticket_counter'] += 1
    return data['ticket_counter']

def get_category_display_name(guild_id, category_key):
    """Obtenir le nom d'affichage d'une catégorie"""
    data = get_guild_data(guild_id)
    categories = data['ticket_categories']
    
    if category_key in categories:
        return categories[category_key]['name']
    
    # Fallback pour les catégories par défaut
    fallback_names = {
        'support': 'Support',
        'bug': 'Bug Report', 
        'other': 'Autre'
    }
    return fallback_names.get(category_key, category_key)

def create_ticket_embed(guild_id):
    """Créer l'embed des tickets avec la configuration sauvegardée"""
    data = get_guild_data(guild_id)
    embed_config = data['config']['ticket_embed']
    
    try:
        color_value = int(embed_config['color'].replace('#', ''), 16)
    except:
        color_value = 0xa30174
    
    embed = discord.Embed(
        title=embed_config['title'],
        description=embed_config['description'],
        color=color_value
    )
    
    if embed_config['image_url']:
        embed.set_image(url=embed_config['image_url'])
    
    if embed_config['thumbnail_url']:
        embed.set_thumbnail(url=embed_config['thumbnail_url'])
    
    return embed

def refresh_ticket_categories(guild_id):
    """Fonction pour rafraîchir et valider les catégories de tickets"""
    data = get_guild_data(guild_id)
    categories = data['ticket_categories']
    
    # Vérifier si les catégories par défaut existent, sinon les recréer
    default_categories = {
        'support': {'name': 'Support', 'description': 'Support technique', 'emoji': '🛠️'},
        'bug': {'name': 'Bug Report', 'description': 'Signaler un bug', 'emoji': '🐛'},
        'other': {'name': 'Autre', 'description': 'Autres demandes', 'emoji': '❓'}
    }
    
    # Ajouter les catégories par défaut si elles n'existent pas
    for key, default_data in default_categories.items():
        if key not in categories:
            categories[key] = default_data
    
    return categories

def create_ticket_options(guild_id):
    """Créer les options pour le sélecteur de tickets avec les catégories à jour"""
    categories = refresh_ticket_categories(guild_id)
    options = []
    
    for key, cat_data in categories.items():
        emoji = cat_data.get('emoji', '🎫')
        name = cat_data.get('name', key)
        description = cat_data.get('description', 'Aucune description')
        
        options.append(discord.SelectOption(
            label=name,
            description=description,
            value=key,
            emoji=emoji
        ))
    
    return options

async def create_ticket_transcript(channel):
    """Créer un transcript complet du ticket"""
    messages_list = []
    
    # Récupérer tous les messages
    async for message in channel.history(limit=None, oldest_first=True):
        # Format du timestamp
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
        
        # Contenu du message
        content = message.content if message.content else "[Aucun contenu texte]"
        
        # Ajouter les embeds
        if message.embeds:
            for embed in message.embeds:
                if embed.title:
                    content += f"\n[EMBED] Titre: {embed.title}"
                if embed.description:
                    content += f"\n[EMBED] Description: {embed.description}"
        
        # Ajouter les pièces jointes
        if message.attachments:
            for attachment in message.attachments:
                content += f"\n[FICHIER] {attachment.filename} - {attachment.url}"
        
        # Format de la ligne
        line = f"[{timestamp}] {message.author.name}#{message.author.discriminator} ({message.author.id}): {content}"
        messages_list.append(line)
    
    # Créer le transcript
    transcript = "\n".join(messages_list)
    return transcript

async def send_ticket_log(guild, channel_name, ticket_info, transcript, closed_by):
    """Envoyer les logs du ticket dans le salon dédié"""
    data = get_guild_data(guild.id)
    log_channel_id = data['config']['ticket_logs_channel']
    
    if not log_channel_id:
        return None
    
    log_channel = guild.get_channel(log_channel_id)
    if not log_channel:
        return None
    
    # Créer le fichier transcript
    transcript_file = discord.File(
        io.StringIO(transcript),
        filename=f"transcript-{channel_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
    )
    
    # Créer l'embed de log
    embed = discord.Embed(
        title="📋 Ticket Supprimé",
        color=0xff0000,
        timestamp=datetime.now()
    )
    
    embed.add_field(name="🎫 Salon", value=channel_name, inline=True)
    embed.add_field(name="📊 Numéro", value=ticket_info.get('number', 'N/A'), inline=True)
    embed.add_field(name="🏷️ Catégorie", value=ticket_info.get('category', 'N/A'), inline=True)
    embed.add_field(name="👤 Créé par", value=ticket_info.get('creator', 'N/A'), inline=True)
    embed.add_field(name="🗑️ Supprimé par", value=f"{closed_by.mention} ({closed_by.id})", inline=True)
    embed.add_field(name="⏱️ Fermé le", value=f"<t:{int(datetime.now().timestamp())}:F>", inline=True)
    
    embed.set_footer(text=f"Ticket #{ticket_info.get('number', 'N/A')}")
    
    try:
        log_message = await log_channel.send(embed=embed, file=transcript_file)
        return log_message
    except Exception as e:
        print(f"Erreur lors de l'envoi du log: {e}")
        return None

@bot.event
async def on_ready():
    print(f'{bot.user} est connecté!')
    print(f'Bot ID: {bot.user.id}')
    print(f'Serveurs: {len(bot.guilds)}')
    
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synchronisé {len(synced)} commande(s) globalement")
        
        if synced:
            print("\n📋 Liste des commandes synchronisées:")
            for cmd in synced[:20]:
                print(f"  - /{cmd.name}")
            if len(synced) > 20:
                print(f"  ... et {len(synced) - 20} autres commandes")
        
    except Exception as e:
        print(f"❌ Erreur lors de la synchronisation : {e}")
    
    cleanup_temp_voice.start()
    check_ticket_inactivity.start()  # NOUVEAU
    print("🔄 Task d'inactivité des tickets démarré")
    
@bot.event
async def on_member_join(member):
    """Gestion des nouveaux membres"""
    data = get_guild_data(member.guild.id)
    config = data['config']
    
    # Autorôle
    if config['autorole']:
        try:
            role = member.guild.get_role(config['autorole'])
            if role:
                await member.add_roles(role)
        except:
            pass
    
    # Message de bienvenue
    if config['welcome_channel']:
        try:
            channel = bot.get_channel(config['welcome_channel'])
            if channel:
                message = config['welcome_message'].replace('{user}', member.mention)
                await channel.send(message)
        except:
            pass

@bot.event
async def on_message(message):
    if message.author.bot:
        return
        
    guild_id = message.guild.id
    data = get_guild_data(guild_id)
    config = data['config']
    
    
    # Auto-modération
    if config['automod']:
        # Anti-lien
        if config['antilink']['status'] and any(domain in message.content.lower() for domain in ['http://', 'https://'] if not any(whitelist in message.content.lower() for whitelist in config['whitelist_domains'])):
            action = config['antilink']['action']
            await handle_automod_action(message, action, "Lien non autorisé")
            return
        
        # Mots interdits
        if any(badword in message.content.lower() for badword in config['badwords']):
            await handle_automod_action(message, config['badword_action'], "Mot interdit")
            return
    
    # Sticky messages
    if guild_id in sticky_messages and message.channel.id in sticky_messages[guild_id]:
        await handle_sticky_message(message)
    
    # NOUVEAU : Détecter l'activité dans les tickets
    if message.channel.name.startswith('ticket-'):
        # Ne compter que les messages des utilisateurs (pas du bot ou staff)
        ticket_roles = config.get('ticket_roles', [])
        user_roles = [role.id for role in message.author.roles]
        is_staff = any(role_id in user_roles for role_id in ticket_roles)
        
        # Si c'est un utilisateur normal (pas staff, pas bot)
        if not is_staff and not message.author.bot:
            # Récupérer l'ID du créateur depuis le tracker
            if guild_id in ticket_activity_tracker:
                if message.channel.id in ticket_activity_tracker[guild_id]:
                    creator_id = ticket_activity_tracker[guild_id][message.channel.id]['creator_id']
                    # Si c'est le créateur qui parle, reset l'activité
                    if message.author.id == creator_id:
                        print(f"[INACTIVITY] Activité détectée dans {message.channel.name} par le créateur")
                        update_ticket_activity(guild_id, message.channel.id, creator_id)
                        
                        # Supprimer le message d'avertissement s'il existe
                        warning_msg_id = ticket_activity_tracker[guild_id][message.channel.id].get('warning_message_id')
                        if warning_msg_id:
                            try:
                                warning_msg = await message.channel.fetch_message(warning_msg_id)
                                await warning_msg.delete()
                                print(f"[INACTIVITY] Message d'avertissement supprimé")
                            except:
                                pass
                        
                        # Reset le flag warning
                        ticket_activity_tracker[guild_id][message.channel.id]['warning_sent'] = False
                        ticket_activity_tracker[guild_id][message.channel.id]['warning_message_id'] = None
    
    await bot.process_commands(message)

async def handle_automod_action(message, action, reason):
    """Gère les actions d'auto-modération"""
    try:
        await message.delete()
        
        if action == 'warn':
            await add_warning(message.author, message.guild, reason)
            await message.channel.send(f"⚠️ {message.author.mention}, {reason.lower()}!", delete_after=5)
        elif action == 'kick':
            await message.author.kick(reason=reason)
            await message.channel.send(f"👢 {message.author} a été exclu pour : {reason}", delete_after=5)
        elif action == 'ban':
            await message.author.ban(reason=reason)
            await message.channel.send(f"🔨 {message.author} a été banni pour : {reason}", delete_after=5)
    except:
        pass

async def add_warning(member, guild, reason):
    """Ajouter un avertissement"""
    guild_id = guild.id
    user_id = member.id
    
    if guild_id not in warnings:
        warnings[guild_id] = {}
    if user_id not in warnings[guild_id]:
        warnings[guild_id][user_id] = []
    
    warning = {
        'reason': reason,
        'date': datetime.now().isoformat(),
        'moderator': 'Auto-Modération'
    }
    warnings[guild_id][user_id].append(warning)

async def handle_sticky_message(message):
    """Gère les messages sticky"""
    guild_id = message.guild.id
    channel_id = message.channel.id
    
    if guild_id in sticky_messages and channel_id in sticky_messages[guild_id]:
        sticky_data = sticky_messages[guild_id][channel_id]
        if sticky_data['active']:
            try:
                # Supprimer l'ancien message sticky
                if sticky_data['message_id']:
                    old_message = await message.channel.fetch_message(sticky_data['message_id'])
                    await old_message.delete()
            except:
                pass
            
            # Envoyer le nouveau message sticky
            try:
                embed = discord.Embed(description=sticky_data['content'], color=0xa30174)
                embed.set_author(name=sticky_data['bot_name'])
                new_message = await message.channel.send(embed=embed)
                sticky_messages[guild_id][channel_id]['message_id'] = new_message.id
            except:
                pass

@tasks.loop(minutes=1)
async def cleanup_temp_voice():
    """Nettoie les salons vocaux temporaires vides"""
    to_remove = set()
    for channel_id in list(temp_voice_channels):  # Convertir en liste pour éviter les erreurs
        try:
            channel = bot.get_channel(channel_id)
            if channel and len(channel.members) == 0:
                await channel.delete(reason="Salon vocal temporaire vide")
                to_remove.add(channel_id)
        except Exception as e:
            print(f"Erreur lors du nettoyage du salon vocal {channel_id}: {e}")
            to_remove.add(channel_id)
    
    # Retirer les channels supprimés ou en erreur
    for channel_id in to_remove:
        temp_voice_channels.discard(channel_id)

async def log_action(guild, action, target, moderator, reason):
    """Enregistre une action dans les logs"""
    data = get_guild_data(guild.id)
    if data['config']['logs_channel']:
        try:
            channel = guild.get_channel(data['config']['logs_channel'])
            if channel:
                embed = discord.Embed(
                    title=f"📋 {action}",
                    color=0x0099ff,
                    timestamp=datetime.now()
                )
                embed.add_field(name="Utilisateur", value=f"{target} ({target.id})", inline=True)
                embed.add_field(name="Modérateur", value=f"{moderator} ({moderator.id})", inline=True)
                embed.add_field(name="Raison", value=reason, inline=False)
                await channel.send(embed=embed)
        except:
            pass

@tasks.loop(hours=1)
async def check_ticket_inactivity():
    """Vérifier l'inactivité des tickets toutes les heures"""
    print("\n[INACTIVITY] ========== Vérification des tickets inactifs ==========")
    
    for guild in bot.guilds:
        guild_id = guild.id
        data = get_guild_data(guild_id)
        config = data['config']['inactivity_config']
        
        # Si le système est désactivé, passer
        if not config['enabled']:
            continue
        
        print(f"[INACTIVITY] Vérification pour {guild.name}")
        
        # Parcourir tous les salons texte
        for channel in guild.text_channels:
            # Vérifier si c'est un ticket
            if not channel.name.startswith('ticket-'):
                continue
            
            channel_id = channel.id
            
            # Vérifier si le ticket est dans le tracker
            if guild_id not in ticket_activity_tracker:
                continue
            if channel_id not in ticket_activity_tracker[guild_id]:
                # Ticket pas encore tracké, l'ajouter
                # Essayer d'extraire le créateur depuis le topic
                creator_id = None
                if channel.topic and "ID:" in channel.topic:
                    try:
                        user_id_str = channel.topic.split("ID:")[1].split(")")[0].strip()
                        creator_id = int(user_id_str)
                        update_ticket_activity(guild_id, channel_id, creator_id)
                        print(f"[INACTIVITY] Ticket {channel.name} ajouté au suivi")
                    except:
                        pass
                continue
            
            # Obtenir les infos du ticket
            ticket_info = ticket_activity_tracker[guild_id][channel_id]
            creator_id = ticket_info['creator_id']
            warning_sent = ticket_info['warning_sent']
            extensions = ticket_info.get('extensions', 0)
            
            # Calculer l'inactivité
            hours_inactive = get_ticket_inactivity_hours(guild_id, channel_id)
            delay_hours = config['delay_hours']
            final_close_hours = config['final_close_hours']
            
            print(f"[INACTIVITY] {channel.name}: {hours_inactive}h d'inactivité (warning_sent={warning_sent})")
            
            # CAS 1: Fermeture automatique après 48h (warning déjà envoyé et pas de réponse)
            if warning_sent and hours_inactive >= final_close_hours:
                print(f"[INACTIVITY] ⚠️ Fermeture automatique de {channel.name} (48h dépassées)")
                
                # Créer le transcript
                try:
                    transcript_text = await create_ticket_transcript(channel)
                except Exception as e:
                    transcript_text = f"Erreur: {e}"
                
                # Extraire les infos
                channel_parts = channel.name.split('-')
                ticket_number = channel_parts[-1] if len(channel_parts) >= 4 else "N/A"
                ticket_category = channel_parts[1].title() if len(channel_parts) >= 4 else "N/A"
                creator_user = guild.get_member(creator_id)
                
                ticket_info_dict = {
                    'number': ticket_number,
                    'category': ticket_category,
                    'creator': creator_user.mention if creator_user else "Inconnu"
                }
                
                # Message de fermeture dans le ticket
                try:
                    close_embed = discord.Embed(
                        title="🔒 Ticket Fermé Automatiquement",
                        description="Ce ticket a été fermé automatiquement après 48h d'inactivité sans réponse.",
                        color=0xff0000,
                        timestamp=datetime.now()
                    )
                    await channel.send(embed=close_embed)
                except:
                    pass
                
                await asyncio.sleep(3)
                
                # Envoyer les logs
                await send_ticket_log(
                    guild,
                    channel.name,
                    ticket_info_dict,
                    transcript_text,
                    bot.user  # Fermé par le bot
                )
                
                # Envoyer en DM
                if creator_user:
                    try:
                        dm_embed = discord.Embed(
                            title="📄 Transcript de votre ticket",
                            description="**Raison:** Fermé automatiquement après 48h d'inactivité",
                            color=0xff0000,
                            timestamp=datetime.now()
                        )
                        dm_embed.add_field(name="🎫 Ticket", value=channel.name, inline=True)
                        dm_embed.add_field(name="📊 Numéro", value=f"#{ticket_number}", inline=True)
                        dm_embed.add_field(name="🏷️ Catégorie", value=ticket_category, inline=True)
                        dm_embed.set_footer(text=f"Serveur: {guild.name}")
                        
                        filename = f"transcript-{channel.name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
                        file_for_dm = discord.File(io.StringIO(transcript_text), filename=filename)
                        await creator_user.send(embed=dm_embed, file=file_for_dm)
                        print(f"[INACTIVITY] ✅ DM envoyé à {creator_user.name}")
                    except:
                        pass
                
                # Retirer du tracker
                remove_ticket_from_tracker(guild_id, channel_id)
                
                # Supprimer le salon
                try:
                    await channel.delete(reason="Fermeture automatique après 48h d'inactivité")
                    print(f"[INACTIVITY] ✅ {channel.name} supprimé")
                except Exception as e:
                    print(f"[INACTIVITY] ❌ Erreur suppression: {e}")
                
                continue
            
            # CAS 2: Envoi du message d'avertissement après 24h
            if not warning_sent and hours_inactive >= delay_hours:
                print(f"[INACTIVITY] 📢 Envoi avertissement pour {channel.name}")
                
                # Récupérer le créateur
                creator_user = guild.get_member(creator_id)
                if not creator_user:
                    print(f"[INACTIVITY] ⚠️ Créateur introuvable pour {channel.name}")
                    continue
                
                # Créer l'embed personnalisé
                embed_config = config['embed']
                try:
                    color_value = int(embed_config['color'].replace('#', ''), 16)
                except:
                    color_value = 0xff9900
                
                description = embed_config['description'].replace('{hours}', str(int(hours_inactive))).replace('{mention}', creator_user.mention)
                
                embed = discord.Embed(
                    title=embed_config['title'],
                    description=description,
                    color=color_value,
                    timestamp=datetime.now()
                )
                
                if embed_config.get('image_url'):
                    embed.set_image(url=embed_config['image_url'])
                
                embed.set_footer(text=f"Ticket inactif depuis {int(hours_inactive)}h")
                
                # Créer la view avec les boutons
                view = InactivityView(guild_id, channel_id, creator_id)
                
                # Envoyer le message
                try:
                    warning_message = await channel.send(content=creator_user.mention, embed=embed, view=view)
                    
                    # Mettre à jour le tracker
                    ticket_activity_tracker[guild_id][channel_id]['warning_sent'] = True
                    ticket_activity_tracker[guild_id][channel_id]['warning_message_id'] = warning_message.id
                    
                    print(f"[INACTIVITY] ✅ Avertissement envoyé dans {channel.name}")
                    
                    # Notifier le staff si activé
                    if config['notify_staff'] and data['config']['ticket_roles']:
                        staff_mentions = []
                        for role_id in data['config']['ticket_roles']:
                            role = guild.get_role(role_id)
                            if role:
                                staff_mentions.append(role.mention)
                        
                        if staff_mentions:
                            staff_notif = discord.Embed(
                                title="⚠️ Ticket Inactif - Notification Staff",
                                description=f"Le ticket {channel.mention} est inactif depuis {int(hours_inactive)}h.",
                                color=0xffa500
                            )
                            await channel.send(content=" ".join(staff_mentions), embed=staff_notif, delete_after=60)
                    
                except Exception as e:
                    print(f"[INACTIVITY] ❌ Erreur envoi avertissement: {e}")
    
    print("[INACTIVITY] ========== Fin de la vérification ==========\n")

# COMMANDES GIVEAWAYS
@bot.tree.command(name="gcreate", description="Créer un giveaway avec panneau interactif")
async def gcreate(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    await interaction.response.send_modal(GiveawayModal())

@bot.tree.command(name="greroll", description="Relancer un giveaway")
@app_commands.describe(message_id="ID du message du giveaway")
async def greroll(interaction: discord.Interaction, message_id: str):
    if not await check_permissions(interaction):
        return
    try:
        msg_id = int(message_id)
        if msg_id in giveaways and giveaways[msg_id]['participants']:
            winner = random.choice(giveaways[msg_id]['participants'])
            embed = discord.Embed(title="🎉 Giveaway Relancé!", description=f"**Prix:** {giveaways[msg_id]['prize']}\n**Nouveau Gagnant:** <@{winner}>", color=0xa30174)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message("❌ Giveaway introuvable ou aucun participant!", ephemeral=True)
    except: 
        await interaction.response.send_message("❌ ID invalide!", ephemeral=True)

@bot.tree.command(name="glist", description="Lister les giveaways actifs")
async def glist(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    active = [g for g in giveaways.values() if g['active']]
    if not active:
        await interaction.response.send_message("❌ Aucun giveaway actif!", ephemeral=True)
        return
    embed = discord.Embed(title="📊 Giveaways Actifs", color=0xa30174)
    for g in active[:10]:
        embed.add_field(name=f"🎁 {g['prize']}", value=f"Participants: {len(g['participants'])}\nFin: <t:{int(g['end_time'].timestamp())}:R>", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="gend", description="Terminer un giveaway prématurément")
@app_commands.describe(message_id="ID du message du giveaway")
async def gend(interaction: discord.Interaction, message_id: str):
    if not await check_permissions(interaction):
        return
    try:
        msg_id = int(message_id)
        if msg_id in giveaways and giveaways[msg_id]['active']:
            g = giveaways[msg_id]
            g['active'] = False
            if g['participants']:
                winner = random.choice(g['participants'])
                embed = discord.Embed(title="🎉 Giveaway Terminé!", description=f"**Prix:** {g['prize']}\n**Gagnant:** <@{winner}>", color=0xa30174)
                await interaction.response.send_message(embed=embed)
            else:
                await interaction.response.send_message("❌ Aucun participant!")
        else:
            await interaction.response.send_message("❌ Giveaway introuvable!", ephemeral=True)
    except: 
        await interaction.response.send_message("❌ ID invalide!", ephemeral=True)

@bot.tree.command(name="gdelete", description="Supprimer un giveaway")
@app_commands.describe(message_id="ID du message du giveaway")
async def gdelete(interaction: discord.Interaction, message_id: str):
    if not await check_permissions(interaction):
        return
    try:
        msg_id = int(message_id)
        if msg_id in giveaways:
            del giveaways[msg_id]
            await interaction.response.send_message("✅ Giveaway supprimé!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Introuvable!", ephemeral=True)
    except: 
        await interaction.response.send_message("❌ ID invalide!", ephemeral=True)

# COMMANDES MODÉRATION
@bot.tree.command(name="ban", description="Bannir un membre")
@app_commands.describe(member="Membre à bannir", reason="Raison du bannissement")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "Aucune raison"):
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    try:
        await member.ban(reason=reason)
        embed = discord.Embed(title="🔨 Membre Banni", description=f"**Membre:** {member.mention}\n**Raison:** {reason}\n**Modérateur:** {interaction.user.mention}", color=0xff0000)
        await interaction.response.send_message(embed=embed)
        await log_action(interaction.guild, "BAN", member, interaction.user, reason)
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur: {str(e)}", ephemeral=True)

@bot.tree.command(name="kick", description="Exclure un membre")
@app_commands.describe(member="Membre à exclure", reason="Raison de l'exclusion")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "Aucune raison"):
    if not interaction.user.guild_permissions.kick_members:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    try:
        await member.kick(reason=reason)
        embed = discord.Embed(title="👢 Membre Exclu", description=f"**Membre:** {member.mention}\n**Raison:** {reason}\n**Modérateur:** {interaction.user.mention}", color=0xffa500)
        await interaction.response.send_message(embed=embed)
        await log_action(interaction.guild, "KICK", member, interaction.user, reason)
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur: {str(e)}", ephemeral=True)

@bot.tree.command(name="mute", description="Rendre muet un membre")
@app_commands.describe(member="Membre à rendre muet", duration="Durée en minutes", reason="Raison du mute")
async def mute(interaction: discord.Interaction, member: discord.Member, duration: int, reason: str = "Aucune raison"):
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    try:
        until = datetime.now() + timedelta(minutes=duration)
        await member.edit(timed_out_until=until, reason=reason)
        embed = discord.Embed(title="🔇 Membre Muet", description=f"**Membre:** {member.mention}\n**Durée:** {duration}min\n**Raison:** {reason}\n**Modérateur:** {interaction.user.mention}", color=0xffff00)
        await interaction.response.send_message(embed=embed)
        await log_action(interaction.guild, "MUTE", member, interaction.user, f"{reason} ({duration}min)")
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur: {str(e)}", ephemeral=True)

@bot.tree.command(name="unmute", description="Enlever le mute")
@app_commands.describe(member="Membre à démuter")
async def unmute(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    try:
        await member.edit(timed_out_until=None)
        embed = discord.Embed(title="🔊 Membre Démuté", description=f"**Membre:** {member.mention}\n**Modérateur:** {interaction.user.mention}", color=0xa30174)
        await interaction.response.send_message(embed=embed)
        await log_action(interaction.guild, "UNMUTE", member, interaction.user, "Démute")
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur: {str(e)}", ephemeral=True)

@bot.tree.command(name="unban", description="Débannir un utilisateur")
@app_commands.describe(user_id="ID de l'utilisateur à débannir")
async def unban(interaction: discord.Interaction, user_id: str):
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user)
        await interaction.response.send_message(f"✅ {user} a été débanni!")
        await log_action(interaction.guild, "UNBAN", user, interaction.user, "Débannissement")
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur: {str(e)}", ephemeral=True)

@bot.tree.command(name="clear", description="Supprimer des messages")
@app_commands.describe(amount="Nombre de messages à supprimer")
async def clear(interaction: discord.Interaction, amount: int):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    try:
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.response.send_message(f"✅ {len(deleted)} messages supprimés!", ephemeral=True, delete_after=5)
        await log_action(interaction.guild, "CLEAR", interaction.channel, interaction.user, f"{len(deleted)} messages")
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur: {str(e)}", ephemeral=True)

@bot.tree.command(name="warn", description="Donner un avertissement")
@app_commands.describe(member="Membre à avertir", reason="Raison de l'avertissement")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    
    guild_id, user_id = interaction.guild.id, member.id
    if guild_id not in warnings: 
        warnings[guild_id] = {}
    if user_id not in warnings[guild_id]: 
        warnings[guild_id][user_id] = []
    
    warning = {'reason': reason, 'date': datetime.now().isoformat(), 'moderator': str(interaction.user)}
    warnings[guild_id][user_id].append(warning)
    
    embed = discord.Embed(title="⚠️ Avertissement", description=f"**Membre:** {member.mention}\n**Raison:** {reason}\n**Modérateur:** {interaction.user.mention}\n**Total warnings:** {len(warnings[guild_id][user_id])}", color=0xffaa00)
    await interaction.response.send_message(embed=embed)
    await log_action(interaction.guild, "WARN", member, interaction.user, reason)

@bot.tree.command(name="clearwarnings", description="Effacer les avertissements")
@app_commands.describe(member="Membre dont effacer les avertissements")
async def clearwarnings(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    
    guild_id, user_id = interaction.guild.id, member.id
    if guild_id in warnings and user_id in warnings[guild_id]:
        warnings[guild_id][user_id] = []
        await interaction.response.send_message(f"✅ Avertissements de {member.mention} effacés!")
    else:
        await interaction.response.send_message("❌ Aucun avertissement trouvé!", ephemeral=True)

@bot.tree.command(name="nuke", description="Supprimer tous les messages du salon")
async def nuke(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    
    channel = interaction.channel
    position = channel.position
    await interaction.response.send_message("💥 Salon en cours de recréation...", ephemeral=True)
    
    new_channel = await channel.clone()
    await new_channel.edit(position=position)
    await channel.delete()
    
    embed = discord.Embed(title="💥 SALON NUKÉD", description=f"Salon recréé par {interaction.user.mention}", color=0xff0000)
    await new_channel.send(embed=embed)

@bot.tree.command(name="locksalon", description="Verrouiller un salon")
@app_commands.describe(channel="Salon à verrouiller")
async def locksalon(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    
    if not channel: 
        channel = interaction.channel
    
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    
    embed = discord.Embed(title="🔒 Salon Verrouillé", description=f"**Salon:** {channel.mention}\n**Par:** {interaction.user.mention}", color=0xff0000)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="unlocksalon", description="Déverrouiller un salon")
@app_commands.describe(channel="Salon à déverrouiller")
async def unlocksalon(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    
    if not channel: 
        channel = interaction.channel
    
    overwrite = channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    
    embed = discord.Embed(title="🔓 Salon Déverrouillé", description=f"**Salon:** {channel.mention}\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="embed", description="Créer un embed avec panneau interactif")
async def embed(interaction: discord.Interaction):
    await interaction.response.send_modal(EmbedModalComplete())

@bot.tree.command(name="slowmode", description="Configurer le mode lent")
@app_commands.describe(seconds="Délai en secondes", channel="Salon à configurer")
async def slowmode(interaction: discord.Interaction, seconds: int, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    
    if not channel: 
        channel = interaction.channel
    
    await channel.edit(slowmode_delay=seconds)
    embed = discord.Embed(title="🐌 Mode Lent Activé", description=f"**Salon:** {channel.mention}\n**Délai:** {seconds}s\n**Par:** {interaction.user.mention}", color=0xffaa00)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="removeslowmode", description="Supprimer le mode lent")
@app_commands.describe(channel="Salon à configurer")
async def removeslowmode(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    
    if not channel: 
        channel = interaction.channel
    
    await channel.edit(slowmode_delay=0)
    embed = discord.Embed(title="🚀 Mode Lent Désactivé", description=f"**Salon:** {channel.mention}\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="massban", description="Bannissement en masse (nécessite fichier .txt)")
@app_commands.describe(reason="Raison du bannissement")
async def massban(interaction: discord.Interaction, reason: str = "Bannissement en masse"):
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    
    await interaction.response.send_message("📄 Veuillez joindre un fichier .txt avec les IDs utilisateurs (un par ligne)", ephemeral=True)

# AUTO-MODÉRATION
@bot.tree.command(name="automod", description="Configurer l'auto-modération générale")
@app_commands.describe(status="Activer ou désactiver l'auto-modération")
async def automod(interaction: discord.Interaction, status: bool):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['config']['automod'] = status
    
    embed = discord.Embed(title="🛡️ Auto-Modération", description=f"**Status:** {'✅ Activé' if status else '❌ Désactivé'}\n**Par:** {interaction.user.mention}", color=0xa30174 if status else 0xff0000)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="antilink_config", description="Configurer l'anti-lien")
@app_commands.describe(status="on/off", action="warn/kick/ban")
async def antilink_config(interaction: discord.Interaction, status: str, action: str):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['config']['antilink'] = {'status': status.lower() == 'on', 'action': action}
    
    embed = discord.Embed(title="🔗 Anti-Lien Configuré", description=f"**Status:** {'✅ Activé' if status.lower() == 'on' else '❌ Désactivé'}\n**Action:** {action}\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="antispam_config", description="Configurer l'anti-spam")
@app_commands.describe(status="on/off", action="warn/kick/ban")
async def antispam_config(interaction: discord.Interaction, status: str, action: str):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['config']['antispam'] = {'status': status.lower() == 'on', 'action': action}
    
    embed = discord.Embed(title="🚫 Anti-Spam Configuré", description=f"**Status:** {'✅ Activé' if status.lower() == 'on' else '❌ Désactivé'}\n**Action:** {action}\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="antiraid_config", description="Configurer l'anti-raid")
@app_commands.describe(status="on/off", action="warn/kick/ban")
async def antiraid_config(interaction: discord.Interaction, status: str, action: str):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['config']['antiraid'] = {'status': status.lower() == 'on', 'action': action}
    
    embed = discord.Embed(title="🛡️ Anti-Raid Configuré", description=f"**Status:** {'✅ Activé' if status.lower() == 'on' else '❌ Désactivé'}\n**Action:** {action}\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="antilink", description="Activer/désactiver l'anti-lien")
@app_commands.describe(status="True/False")
async def antilink(interaction: discord.Interaction, status: bool):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['config']['antilink']['status'] = status
    
    embed = discord.Embed(title="🔗 Anti-Lien", description=f"**Status:** {'✅ Activé' if status else '❌ Désactivé'}\n**Par:** {interaction.user.mention}", color=0xa30174 if status else 0xff0000)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="antilinkaction", description="Configurer l'action anti-lien")
@app_commands.describe(action="warn/kick/ban")
async def antilinkaction(interaction: discord.Interaction, action: str):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['config']['antilink']['action'] = action
    
    embed = discord.Embed(title="🔗 Action Anti-Lien", description=f"**Action:** {action}\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="whitelist_add", description="Ajouter un domaine autorisé")
@app_commands.describe(domain="Domaine à ajouter (ex: youtube.com)")
async def whitelist_add(interaction: discord.Interaction, domain: str):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    if domain not in data['config']['whitelist_domains']:
        data['config']['whitelist_domains'].append(domain)
        await interaction.response.send_message(f"✅ Domaine `{domain}` ajouté à la liste blanche!")
    else:
        await interaction.response.send_message(f"❌ Domaine `{domain}` déjà dans la liste!", ephemeral=True)

@bot.tree.command(name="whitelist_remove", description="Retirer un domaine autorisé")
@app_commands.describe(domain="Domaine à retirer")
async def whitelist_remove(interaction: discord.Interaction, domain: str):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    if domain in data['config']['whitelist_domains']:
        data['config']['whitelist_domains'].remove(domain)
        await interaction.response.send_message(f"✅ Domaine `{domain}` retiré de la liste blanche!")
    else:
        await interaction.response.send_message(f"❌ Domaine `{domain}` introuvable!", ephemeral=True)

@bot.tree.command(name="whitelist_list", description="Voir la liste blanche des domaines")
async def whitelist_list(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    domains = data['config']['whitelist_domains']
    
    embed = discord.Embed(title="📋 Liste Blanche des Domaines", color=0xa30174)
    embed.description = "\n".join([f"• {domain}" for domain in domains]) or "Aucun domaine"
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="badwordaction", description="Configurer l'action pour les mots interdits")
@app_commands.describe(action="warn/kick/ban")
async def badwordaction(interaction: discord.Interaction, action: str):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['config']['badword_action'] = action
    
    embed = discord.Embed(title="🚫 Action Mots Interdits", description=f"**Action:** {action}\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="addword", description="Ajouter un mot interdit")
@app_commands.describe(word="Mot à interdire")
async def addword(interaction: discord.Interaction, word: str):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    if word.lower() not in data['config']['badwords']:
        data['config']['badwords'].append(word.lower())
        await interaction.response.send_message(f"✅ Mot `{word}` ajouté aux mots interdits!")
    else:
        await interaction.response.send_message(f"❌ Mot `{word}` déjà interdit!", ephemeral=True)

@bot.tree.command(name="removeword", description="Retirer un mot interdit")
@app_commands.describe(word="Mot à autoriser")
async def removeword(interaction: discord.Interaction, word: str):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    if word.lower() in data['config']['badwords']:
        data['config']['badwords'].remove(word.lower())
        await interaction.response.send_message(f"✅ Mot `{word}` retiré des mots interdits!")
    else:
        await interaction.response.send_message(f"❌ Mot `{word}` introuvable!", ephemeral=True)

# GESTION RÔLES
@bot.tree.command(name="autorole", description="Configurer le rôle automatique pour les nouveaux membres")
@app_commands.describe(role="Rôle à donner automatiquement")
async def autorole(interaction: discord.Interaction, role: discord.Role):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['config']['autorole'] = role.id
    
    embed = discord.Embed(title="🎭 Autorôle Configuré", description=f"**Rôle:** {role.mention}\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="autorole_remove", description="Supprimer l'autorôle")
async def autorole_remove(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['config']['autorole'] = None
    
    embed = discord.Embed(title="🎭 Autorôle Supprimé", description=f"**Par:** {interaction.user.mention}", color=0xff0000)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="addrole", description="Ajouter un rôle à un membre")
@app_commands.describe(member="Membre", role="Rôle à ajouter")
async def addrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    
    try:
        await member.add_roles(role)
        embed = discord.Embed(title="✅ Rôle Ajouté", description=f"**Membre:** {member.mention}\n**Rôle:** {role.mention}\n**Par:** {interaction.user.mention}", color=0xa30174)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur: {str(e)}", ephemeral=True)

@bot.tree.command(name="removerole", description="Retirer un rôle à un membre")
@app_commands.describe(member="Membre", role="Rôle à retirer")
async def removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    
    try:
        await member.remove_roles(role)
        embed = discord.Embed(title="❌ Rôle Retiré", description=f"**Membre:** {member.mention}\n**Rôle:** {role.mention}\n**Par:** {interaction.user.mention}", color=0xff0000)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur: {str(e)}", ephemeral=True)

# INFORMATIONS
@bot.tree.command(name="userinfo", description="Afficher les informations d'un utilisateur")
@app_commands.describe(member="Membre à analyser")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    if not member: 
        member = interaction.user
    
    embed = discord.Embed(title=f"👤 Informations - {member}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🆔 ID", value=member.id, inline=True)
    embed.add_field(name="📅 Compte créé", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
    embed.add_field(name="📈 Rejoint le serveur", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
    embed.add_field(name="🎭 Rôles", value=f"{len(member.roles)-1}", inline=True)
    embed.add_field(name="📱 Statut", value=str(member.status).title(), inline=True)
    embed.add_field(name="🤖 Bot", value="Oui" if member.bot else "Non", inline=True)
    
    # Warnings
    guild_id, user_id = interaction.guild.id, member.id
    warning_count = len(warnings.get(guild_id, {}).get(user_id, []))
    embed.add_field(name="⚠️ Avertissements", value=warning_count, inline=True)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="serverinfo", description="Afficher les informations du serveur")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    
    embed = discord.Embed(title=f"🏰 Informations - {guild.name}", color=0xa30174)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="🆔 ID", value=guild.id, inline=True)
    embed.add_field(name="👑 Propriétaire", value=guild.owner.mention, inline=True)
    embed.add_field(name="📅 Créé le", value=f"<t:{int(guild.created_at.timestamp())}:R>", inline=True)
    embed.add_field(name="👥 Membres", value=guild.member_count, inline=True)
    embed.add_field(name="📝 Salons", value=len(guild.channels), inline=True)
    embed.add_field(name="🎭 Rôles", value=len(guild.roles), inline=True)
    embed.add_field(name="😀 Emojis", value=len(guild.emojis), inline=True)
    embed.add_field(name="🚀 Niveau Boost", value=guild.premium_tier, inline=True)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="warnings", description="Voir les avertissements d'un membre")
@app_commands.describe(member="Membre à vérifier")
async def warnings_cmd(interaction: discord.Interaction, member: discord.Member = None):
    if not member: 
        member = interaction.user
    
    guild_id, user_id = interaction.guild.id, member.id
    user_warnings = warnings.get(guild_id, {}).get(user_id, [])
    
    embed = discord.Embed(title=f"⚠️ Avertissements - {member}", color=0xffaa00)
    
    if not user_warnings:
        embed.description = "Aucun avertissement"
    else:
        for i, warning in enumerate(user_warnings[-10:], 1):
            embed.add_field(
                name=f"Avertissement {i}",
                value=f"**Raison:** {warning['reason']}\n**Date:** <t:{int(datetime.fromisoformat(warning['date']).timestamp())}:R>\n**Par:** {warning['moderator']}",
                inline=False
            )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="listwords", description="Voir la liste des mots interdits (en privé)")
async def listwords(interaction: discord.Interaction):
    data = get_guild_data(interaction.guild.id)
    badwords = data['config']['badwords']
    
    if not badwords:
        await interaction.response.send_message("❌ Aucun mot interdit configuré!", ephemeral=True)
        return
    
    embed = discord.Embed(title="🚫 Mots Interdits", color=0xff0000)
    embed.description = "\n".join([f"• {word}" for word in badwords[:50]])  # Limite à 50
    
    try:
        await interaction.user.send(embed=embed)
        await interaction.response.send_message("📧 Liste envoyée en privé!", ephemeral=True)
    except:
        await interaction.response.send_message("❌ Impossible d'envoyer en privé! DM fermés?", ephemeral=True)

# CONFIGURATION
@bot.tree.command(name="config", description="Voir la configuration complète du bot")
async def config(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    config = data['config']
    
    embed = discord.Embed(title="⚙️ Configuration du Serveur", color=0x0099ff)
    
    # Logs
    logs_ch = f"<#{config['logs_channel']}>" if config['logs_channel'] else "❌ Non configuré"
    embed.add_field(name="📋 Salon de Logs", value=logs_ch, inline=True)
    
    # Autorôle
    autorole = f"<@&{config['autorole']}>" if config['autorole'] else "❌ Non configuré"
    embed.add_field(name="🎭 Autorôle", value=autorole, inline=True)
    
    # Auto-modération
    automod_status = "✅ Activé" if config['automod'] else "❌ Désactivé"
    embed.add_field(name="🛡️ Auto-modération", value=automod_status, inline=True)
    
    # Anti-lien
    antilink = config['antilink']
    antilink_status = f"{'✅' if antilink['status'] else '❌'} ({antilink['action']})"
    embed.add_field(name="🔗 Anti-lien", value=antilink_status, inline=True)
    
    # Mots interdits
    badwords_count = len(config['badwords'])
    embed.add_field(name="🚫 Mots interdits", value=f"{badwords_count} ({config['badword_action']})", inline=True)
    
    # Bienvenue
    welcome_ch = f"<#{config['welcome_channel']}>" if config['welcome_channel'] else "❌ Non configuré"
    embed.add_field(name="👋 Bienvenue", value=welcome_ch, inline=True)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="setlogs", description="Configurer le salon de logs")
@app_commands.describe(channel="Salon pour les logs")
async def setlogs(interaction: discord.Interaction, channel: discord.TextChannel):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['config']['logs_channel'] = channel.id
    
    embed = discord.Embed(title="📋 Salon de Logs Configuré", description=f"**Salon:** {channel.mention}\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="setlogs_remove", description="Supprimer le salon de logs")
async def setlogs_remove(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['config']['logs_channel'] = None
    
    embed = discord.Embed(title="📋 Salon de Logs Supprimé", description=f"**Par:** {interaction.user.mention}", color=0xff0000)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="setrole", description="Configurer les rôles autorisés à utiliser le bot")
@app_commands.describe(role="Rôle à autoriser")
async def setrole(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Seuls les administrateurs peuvent configurer les rôles autorisés!", ephemeral=True)
        return
    data = get_guild_data(interaction.guild.id)
    if role.id not in data['config']['allowed_roles']:
        data['config']['allowed_roles'].append(role.id)
        await interaction.response.send_message(f"✅ Rôle {role.mention} ajouté aux autorisations!")
    else:
        await interaction.response.send_message(f"❌ Rôle {role.mention} déjà autorisé!", ephemeral=True)

@bot.tree.command(name="unsetroles", description="Retirer un rôle des autorisations")
@app_commands.describe(role="Rôle à retirer")
async def unsetroles(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Seuls les administrateurs peuvent configurer les rôles autorisés!", ephemeral=True)
        return
    data = get_guild_data(interaction.guild.id)
    if role.id in data['config']['allowed_roles']:
        data['config']['allowed_roles'].remove(role.id)
        await interaction.response.send_message(f"✅ Rôle {role.mention} retiré des autorisations!")
    else:
        await interaction.response.send_message(f"❌ Rôle {role.mention} pas dans les autorisations!", ephemeral=True)

# SYSTEME AUTO CLOSE TICKET
@bot.tree.command(name="inactivity-enable", description="Activer/désactiver le système d'inactivité des tickets")
@app_commands.describe(status="True pour activer, False pour désactiver")
async def inactivity_enable(interaction: discord.Interaction, status: bool):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    data['config']['inactivity_config']['enabled'] = status
    
    embed = discord.Embed(
        title="⏰ Système d'Inactivité des Tickets",
        description=f"**Status:** {'✅ Activé' if status else '❌ Désactivé'}\n**Par:** {interaction.user.mention}",
        color=0x00ff00 if status else 0xff0000,
        timestamp=datetime.now()
    )
    
    if status:
        embed.add_field(
            name="ℹ️ Fonctionnement",
            value=f"• Avertissement après **24h** d'inactivité\n• Fermeture automatique après **48h** sans réponse\n• Rappel tous les **24h** si ticket gardé ouvert",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="inactivity-delay", description="Définir le délai avant l'avertissement d'inactivité")
@app_commands.describe(hours="Nombre d'heures d'inactivité avant l'avertissement (défaut: 24)")
async def inactivity_delay(interaction: discord.Interaction, hours: int):
    if not await check_permissions(interaction):
        return
    
    if hours < 1 or hours > 168:  # Max 1 semaine
        await interaction.response.send_message("❌ Le délai doit être entre 1 et 168 heures (1 semaine)!", ephemeral=True)
        return
    
    data = get_guild_data(interaction.guild.id)
    data['config']['inactivity_config']['delay_hours'] = hours
    
    embed = discord.Embed(
        title="⏰ Délai d'Inactivité Configuré",
        description=f"**Nouveau délai:** {hours}h\n**Par:** {interaction.user.mention}",
        color=0xa30174,
        timestamp=datetime.now()
    )
    
    embed.add_field(
        name="ℹ️ Information",
        value=f"Les tickets recevront un avertissement après **{hours}h** d'inactivité.",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="inactivity-notify-staff", description="Activer/désactiver les notifications staff pour les tickets inactifs")
@app_commands.describe(status="True pour activer, False pour désactiver")
async def inactivity_notify_staff(interaction: discord.Interaction, status: bool):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    data['config']['inactivity_config']['notify_staff'] = status
    
    embed = discord.Embed(
        title="🔔 Notifications Staff Inactivité",
        description=f"**Status:** {'✅ Activé' if status else '❌ Désactivé'}\n**Par:** {interaction.user.mention}",
        color=0xa30174,
        timestamp=datetime.now()
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="inactivity-customize", description="Personnaliser le message d'avertissement d'inactivité")
async def inactivity_customize(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    await interaction.response.send_modal(InactivityMessageModal())

@bot.tree.command(name="inactivity-status", description="Voir la configuration et l'état du système d'inactivité")
async def inactivity_status(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    config = data['config']['inactivity_config']
    
    embed = discord.Embed(
        title="⚙️ Configuration Système d'Inactivité",
        color=0x0099ff,
        timestamp=datetime.now()
    )
    
    # Status général
    status_icon = "✅ Activé" if config['enabled'] else "❌ Désactivé"
    embed.add_field(
        name="📊 Status Général",
        value=f"**Système:** {status_icon}\n**Délai avertissement:** {config['delay_hours']}h\n**Fermeture auto:** {config['final_close_hours']}h\n**Notif staff:** {'✅' if config['notify_staff'] else '❌'}",
        inline=False
    )
    
    # Tickets surveillés
    guild_id = interaction.guild.id
    if guild_id in ticket_activity_tracker:
        tracked = ticket_activity_tracker[guild_id]
        if tracked:
            ticket_list = []
            for channel_id, info in list(tracked.items())[:5]:  # Max 5
                channel = interaction.guild.get_channel(channel_id)
                if channel:
                    hours = get_ticket_inactivity_hours(guild_id, channel_id)
                    warning = "⚠️" if info['warning_sent'] else "✅"
                    ticket_list.append(f"{warning} {channel.mention} - {int(hours)}h")
            
            if ticket_list:
                embed.add_field(
                    name=f"🎫 Tickets Surveillés ({len(tracked)})",
                    value="\n".join(ticket_list),
                    inline=False
                )
                
                if len(tracked) > 5:
                    embed.add_field(name="", value=f"... et {len(tracked) - 5} autres tickets", inline=False)
        else:
            embed.add_field(name="🎫 Tickets Surveillés", value="Aucun ticket actuellement surveillé", inline=False)
    else:
        embed.add_field(name="🎫 Tickets Surveillés", value="Aucun ticket actuellement surveillé", inline=False)
    
    # Config embed
    embed_config = config['embed']
    embed.add_field(
        name="💬 Message Configuré",
        value=f"**Titre:** {embed_config['title'][:50]}\n**Couleur:** {embed_config['color']}\n**Bouton garder:** {embed_config['button_keep']}\n**Bouton fermer:** {embed_config['button_close']}",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="inactivity-check", description="[ADMIN] Forcer la vérification d'inactivité maintenant")
async def inactivity_check(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Seuls les administrateurs peuvent utiliser cette commande!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    # Lancer manuellement la vérification
    print("[INACTIVITY] Vérification forcée par admin")
    await check_ticket_inactivity()
    
    await interaction.followup.send("✅ Vérification d'inactivité effectuée !\nConsultez les logs pour voir les résultats.", ephemeral=True)

# SYSTÈME DE VOUCHS
@bot.tree.command(name="vouch", description="Laisser un avis client avec formulaire interactif")
async def vouch(interaction: discord.Interaction):
    await interaction.response.send_modal(VouchModal())

@bot.tree.command(name="modifembed", description="Personnaliser l'apparence des embeds de vouch")
@app_commands.describe(titre="Titre de l'embed", couleur="Couleur hex", footer="Footer", thumbnail="Afficher avatar")
async def modifembed(interaction: discord.Interaction, titre: str, couleur: str, footer: str, thumbnail: bool):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['config']['vouch_config'] = {
        'title': titre,
        'color': couleur,
        'footer': footer,
        'thumbnail': thumbnail
    }
    
    try:
        color_int = int(couleur.replace('#', ''), 16)
    except:
        color_int = 0xa30174
    
    embed = discord.Embed(title="🎨 Configuration Vouch Modifiée", color=color_int)
    embed.add_field(name="Titre", value=titre, inline=True)
    embed.add_field(name="Couleur", value=couleur, inline=True)
    embed.add_field(name="Footer", value=footer, inline=True)
    embed.add_field(name="Thumbnail", value="✅" if thumbnail else "❌", inline=True)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="resetcount", description="Remettre le compteur de vouchs à zéro")
async def resetcount(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['vouch_count'] = 0
    
    embed = discord.Embed(title="🔄 Compteur Reset", description=f"**Compteur de vouchs remis à 0**\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="configembed", description="Voir la configuration actuelle des vouchs")
async def configembed(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    config = data['config']['vouch_config']
    
    try:
        color_int = int(config['color'].replace('#', ''), 16)
    except:
        color_int = 0xa30174
    
    embed = discord.Embed(title="🎨 Configuration Vouchs", color=color_int)
    embed.add_field(name="Titre", value=config['title'], inline=True)
    embed.add_field(name="Couleur", value=config['color'], inline=True)
    embed.add_field(name="Footer", value=config['footer'], inline=True)
    embed.add_field(name="Thumbnail", value="✅" if config['thumbnail'] else "❌", inline=True)
    embed.add_field(name="Compteur Actuel", value=data['vouch_count'], inline=True)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="stats", description="Afficher les statistiques complètes du serveur")
async def stats(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    guild = interaction.guild
    data = get_guild_data(guild.id)
    
    # Calculer stats
    total_warnings = sum(len(user_warns) for user_warns in warnings.get(guild.id, {}).values())
    online_members = len([m for m in guild.members if m.status != discord.Status.offline])
    
    embed = discord.Embed(title="📊 Statistiques du Serveur", color=0xa30174)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    
    embed.add_field(name="👥 Membres", value=f"Total: {guild.member_count}\nEn ligne: {online_members}", inline=True)
    embed.add_field(name="📝 Salons", value=f"Texte: {len(guild.text_channels)}\nVocal: {len(guild.voice_channels)}", inline=True)
    embed.add_field(name="⚠️ Modération", value=f"Warnings: {total_warnings}\nAuto-mod: {'✅' if data['config']['automod'] else '❌'}", inline=True)
    embed.add_field(name="🎁 Giveaways", value=f"Actifs: {len([g for g in giveaways.values() if g['active']])}", inline=True)
    embed.add_field(name="💬 Vouchs", value=data['vouch_count'], inline=True)
    embed.add_field(name="🔑 Clés", value=f"Promo: {len(data['keys'])}\nFree: {len(data['free_keys'])}", inline=True)
    
    await interaction.response.send_message(embed=embed)

# SALONS VOCAUX
@bot.tree.command(name="tempvoice", description="Créer un salon vocal temporaire")
@app_commands.describe(name="Nom du salon", max_users="Limite d'utilisateurs (0 = illimité)")
async def tempvoice(interaction: discord.Interaction, name: str, max_users: int = 0):
    try:
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(connect=True),
            interaction.user: discord.PermissionOverwrite(manage_channels=True)
        }
        
        channel = await interaction.guild.create_voice_channel(
            name=name,
            user_limit=max_users if max_users > 0 else None,
            overwrites=overwrites
        )
        
        temp_voice_channels.add(channel.id)
        
        embed = discord.Embed(title="🔊 Salon Vocal Temporaire Créé", description=f"**Salon:** {channel.mention}\n**Limite:** {max_users if max_users > 0 else 'Aucune'}\n**Créé par:** {interaction.user.mention}", color=0xa30174)
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        await interaction.response.send_message(f"❌ Erreur: {str(e)}", ephemeral=True)


def build_voctemp_embed(channel: discord.VoiceChannel, owner: discord.Member, room_data: dict) -> discord.Embed:
    mode_labels = {
        'open': '🔊 Ouvert',
        'closed': '🔒 Fermé',
        'private': '📣 Privé'
    }
    toggles = room_data['toggles']
    embed = discord.Embed(
        title="⚙️ Configuration du salon vocal temporaire",
        description=(
            f"**Propriétaire :** {owner.mention}\n"
            f"**Salon vocal :** {channel.mention}\n"
            f"**Mode :** {mode_labels.get(room_data['mode'], 'Inconnu')}"
        ),
        color=0xa30174
    )
    embed.add_field(name="✅ Liste blanche", value=', '.join(f"<@{uid}>" for uid in room_data['whitelist']) or "Aucun", inline=False)
    embed.add_field(name="⛔ Liste noire", value=', '.join(f"<@{uid}>" for uid in room_data['blacklist']) or "Aucun", inline=False)
    embed.add_field(
        name="🎛️ Permissions",
        value=(
            f"Micro: {'✅' if toggles['micro'] else '❌'} | "
            f"Vidéo: {'✅' if toggles['video'] else '❌'} | "
            f"Soundboard: {'✅' if toggles['soundboard'] else '❌'} | "
            f"Statut: {'✅' if toggles['status'] else '❌'}"
        ),
        inline=False
    )
    return embed


async def apply_voctemp_mode(channel: discord.VoiceChannel, room_data: dict):
    guild = channel.guild
    everyone = guild.default_role
    mode = room_data['mode']

    if mode == 'open':
        await channel.set_permissions(everyone, view_channel=True, connect=True)
    elif mode == 'closed':
        await channel.set_permissions(everyone, view_channel=True, connect=False)
    else:
        await channel.set_permissions(everyone, view_channel=False, connect=False)

    for user_id in room_data['blacklist']:
        member = guild.get_member(user_id)
        if member:
            await channel.set_permissions(member, connect=False)

    for user_id in room_data['whitelist']:
        member = guild.get_member(user_id)
        if member:
            await channel.set_permissions(member, view_channel=True, connect=True)


async def apply_voctemp_toggles(channel: discord.VoiceChannel, room_data: dict):
    everyone = channel.guild.default_role
    toggles = room_data['toggles']
    await channel.set_permissions(
        everyone,
        speak=toggles['micro'],
        stream=toggles['video'],
        use_soundboard=toggles['soundboard'],
        use_voice_activation=toggles['status']
    )


class VocTempUserModal(discord.ui.Modal):
    def __init__(self, action: str, voice_channel_id: int):
        super().__init__(title=f"Voc Temp • {action}")
        self.action = action
        self.voice_channel_id = voice_channel_id
        self.user_id_input = discord.ui.TextInput(label="ID utilisateur", placeholder="123456789012345678", required=True)
        self.add_item(self.user_id_input)

    async def on_submit(self, interaction: discord.Interaction):
        room_data = voice_temp_rooms.get(self.voice_channel_id)
        if not room_data:
            await interaction.response.send_message("❌ Salon temporaire introuvable.", ephemeral=True)
            return

        if interaction.user.id != room_data['owner_id']:
            await interaction.response.send_message("❌ Seul le propriétaire peut gérer ce panel.", ephemeral=True)
            return

        try:
            target_id = int(str(self.user_id_input.value).strip())
        except ValueError:
            await interaction.response.send_message("❌ ID invalide.", ephemeral=True)
            return

        if self.action == 'whitelist':
            room_data['whitelist'].add(target_id)
            room_data['blacklist'].discard(target_id)
            message = f"✅ <@{target_id}> ajouté à la liste blanche."
        elif self.action == 'blacklist':
            room_data['blacklist'].add(target_id)
            room_data['whitelist'].discard(target_id)
            message = f"✅ <@{target_id}> ajouté à la liste noire."
        else:
            room_data['owner_id'] = target_id
            message = f"👑 Propriété transférée à <@{target_id}>."

        channel = interaction.guild.get_channel(self.voice_channel_id)
        if channel:
            await apply_voctemp_mode(channel, room_data)
            await apply_voctemp_toggles(channel, room_data)
            if self.action == 'owner':
                target_member = interaction.guild.get_member(target_id)
                if target_member:
                    await channel.set_permissions(target_member, manage_channels=True, move_members=True)

        await interaction.response.send_message(message, ephemeral=True)


class VocTempSetupModal(discord.ui.Modal, title="Configuration /voctemp"):
    source_voice_id = discord.ui.TextInput(label="ID du salon vocal déclencheur", placeholder="123456789012345678", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not await check_permissions(interaction):
            return

        try:
            channel_id = int(str(self.source_voice_id).strip())
        except ValueError:
            await interaction.response.send_message("❌ L'ID indiqué est invalide.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message("❌ Ce salon n'est pas un salon vocal valide.", ephemeral=True)
            return

        data = get_guild_data(interaction.guild.id)
        data['config']['voctemp']['source_channel_id'] = channel_id
        await interaction.response.send_message(f"✅ Setup terminé. Salon déclencheur: {channel.mention}", ephemeral=True)


class VocTempSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Configurer l'ID vocal", style=discord.ButtonStyle.primary, emoji="🛠️")
    async def configure(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_permissions(interaction):
            return
        await interaction.response.send_modal(VocTempSetupModal())


class VocTempPanelView(discord.ui.View):
    def __init__(self, voice_channel_id: int):
        super().__init__(timeout=None)
        self.voice_channel_id = voice_channel_id

    async def _owner_guard(self, interaction: discord.Interaction) -> bool:
        room_data = voice_temp_rooms.get(self.voice_channel_id)
        if not room_data:
            await interaction.response.send_message("❌ Ce salon n'existe plus.", ephemeral=True)
            return False
        if interaction.user.id != room_data['owner_id']:
            await interaction.response.send_message("❌ Seul le propriétaire peut utiliser ce panel.", ephemeral=True)
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction):
        channel = interaction.guild.get_channel(self.voice_channel_id)
        room_data = voice_temp_rooms.get(self.voice_channel_id)
        owner = interaction.guild.get_member(room_data['owner_id']) if room_data else None
        if channel and room_data and owner:
            embed = build_voctemp_embed(channel, owner, room_data)
            await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="Ouvert", style=discord.ButtonStyle.success, emoji="🔊", row=0)
    async def mode_open(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_guard(interaction):
            return
        channel = interaction.guild.get_channel(self.voice_channel_id)
        room_data = voice_temp_rooms[self.voice_channel_id]
        room_data['mode'] = 'open'
        await apply_voctemp_mode(channel, room_data)
        await interaction.response.send_message("✅ Mode ouvert activé.", ephemeral=True)
        await self._refresh(interaction)

    @discord.ui.button(label="Fermé", style=discord.ButtonStyle.secondary, emoji="🔒", row=0)
    async def mode_closed(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_guard(interaction):
            return
        channel = interaction.guild.get_channel(self.voice_channel_id)
        room_data = voice_temp_rooms[self.voice_channel_id]
        room_data['mode'] = 'closed'
        await apply_voctemp_mode(channel, room_data)
        await interaction.response.send_message("✅ Mode fermé activé.", ephemeral=True)
        await self._refresh(interaction)

    @discord.ui.button(label="Privé", style=discord.ButtonStyle.secondary, emoji="📣", row=0)
    async def mode_private(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_guard(interaction):
            return
        channel = interaction.guild.get_channel(self.voice_channel_id)
        room_data = voice_temp_rooms[self.voice_channel_id]
        room_data['mode'] = 'private'
        await apply_voctemp_mode(channel, room_data)
        await interaction.response.send_message("✅ Mode privé activé.", ephemeral=True)
        await self._refresh(interaction)

    @discord.ui.button(label="Liste blanche", style=discord.ButtonStyle.primary, emoji="📝", row=1)
    async def whitelist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_guard(interaction):
            return
        await interaction.response.send_modal(VocTempUserModal('whitelist', self.voice_channel_id))

    @discord.ui.button(label="Liste noire", style=discord.ButtonStyle.danger, emoji="📛", row=1)
    async def blacklist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_guard(interaction):
            return
        await interaction.response.send_modal(VocTempUserModal('blacklist', self.voice_channel_id))

    @discord.ui.button(label="Purge", style=discord.ButtonStyle.danger, emoji="⤴️", row=1)
    async def purge(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_guard(interaction):
            return
        channel = interaction.guild.get_channel(self.voice_channel_id)
        room_data = voice_temp_rooms[self.voice_channel_id]
        keepers = set(room_data['whitelist']) | {room_data['owner_id']}
        for member in list(channel.members):
            if member.id not in keepers:
                await member.move_to(None, reason="Purge salon vocal temporaire")
        await interaction.response.send_message("✅ Purge effectuée.", ephemeral=True)

    @discord.ui.button(label="Micro", style=discord.ButtonStyle.secondary, emoji="🎙️", row=2)
    async def toggle_micro(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_guard(interaction):
            return
        channel = interaction.guild.get_channel(self.voice_channel_id)
        room_data = voice_temp_rooms[self.voice_channel_id]
        room_data['toggles']['micro'] = not room_data['toggles']['micro']
        await apply_voctemp_toggles(channel, room_data)
        await interaction.response.send_message("✅ Permission micro mise à jour.", ephemeral=True)
        await self._refresh(interaction)

    @discord.ui.button(label="Vidéo", style=discord.ButtonStyle.secondary, emoji="📹", row=2)
    async def toggle_video(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_guard(interaction):
            return
        channel = interaction.guild.get_channel(self.voice_channel_id)
        room_data = voice_temp_rooms[self.voice_channel_id]
        room_data['toggles']['video'] = not room_data['toggles']['video']
        await apply_voctemp_toggles(channel, room_data)
        await interaction.response.send_message("✅ Permission vidéo mise à jour.", ephemeral=True)
        await self._refresh(interaction)

    @discord.ui.button(label="Soundboards", style=discord.ButtonStyle.secondary, emoji="🎛️", row=2)
    async def toggle_soundboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_guard(interaction):
            return
        channel = interaction.guild.get_channel(self.voice_channel_id)
        room_data = voice_temp_rooms[self.voice_channel_id]
        room_data['toggles']['soundboard'] = not room_data['toggles']['soundboard']
        await apply_voctemp_toggles(channel, room_data)
        await interaction.response.send_message("✅ Permission soundboard mise à jour.", ephemeral=True)
        await self._refresh(interaction)

    @discord.ui.button(label="Statut", style=discord.ButtonStyle.secondary, emoji="📌", row=3)
    async def toggle_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_guard(interaction):
            return
        channel = interaction.guild.get_channel(self.voice_channel_id)
        room_data = voice_temp_rooms[self.voice_channel_id]
        room_data['toggles']['status'] = not room_data['toggles']['status']
        await apply_voctemp_toggles(channel, room_data)
        await interaction.response.send_message("✅ Permission statut mise à jour.", ephemeral=True)
        await self._refresh(interaction)

    @discord.ui.button(label="Transférer la propriété", style=discord.ButtonStyle.primary, emoji="👑", row=4)
    async def transfer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_guard(interaction):
            return
        await interaction.response.send_modal(VocTempUserModal('owner', self.voice_channel_id))


@bot.tree.command(name="voctemp", description="Configurer le système de salons vocaux temporaires")
async def voctemp(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return

    data = get_guild_data(interaction.guild.id)
    current_id = data['config']['voctemp'].get('source_channel_id')
    current_channel = interaction.guild.get_channel(current_id) if current_id else None
    current_text = current_channel.mention if current_channel else "Non configuré"

    embed = discord.Embed(
        title="🛠️ Setup Voc Temp",
        description=(
            "Définissez l'ID du salon vocal **déclencheur**.\n"
            "Quand un membre le rejoint, le bot crée une voc temporaire et le déplace dedans."
        ),
        color=0x5865f2
    )
    embed.add_field(name="Salon actuel", value=current_text, inline=False)
    await interaction.response.send_message(embed=embed, view=VocTempSetupView(), ephemeral=True)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot:
        return

    data = get_guild_data(member.guild.id)
    source_id = data['config']['voctemp'].get('source_channel_id')

    if source_id and after.channel and after.channel.id == source_id:
        category = after.channel.category
        temp_channel = await member.guild.create_voice_channel(
            name=f"🔊 {member.display_name}",
            category=category,
            reason="Création voc temporaire"
        )
        text_channel = await member.guild.create_text_channel(
            name=f"panel-{member.display_name[:12].lower().replace(' ', '-')}",
            category=category,
            reason="Panel voc temporaire"
        )

        room_data = {
            'guild_id': member.guild.id,
            'owner_id': member.id,
            'text_channel_id': text_channel.id,
            'mode': 'open',
            'whitelist': set(),
            'blacklist': set(),
            'toggles': {
                'micro': True,
                'video': True,
                'soundboard': True,
                'status': True
            }
        }
        voice_temp_rooms[temp_channel.id] = room_data
        temp_voice_channels.add(temp_channel.id)

        await temp_channel.set_permissions(member, manage_channels=True, move_members=True, connect=True, view_channel=True)
        await member.move_to(temp_channel)

        embed = build_voctemp_embed(temp_channel, member, room_data)
        await text_channel.send(content=member.mention, embed=embed, view=VocTempPanelView(temp_channel.id))

    for channel in [before.channel]:
        if channel and channel.id in voice_temp_rooms and len(channel.members) == 0:
            room_data = voice_temp_rooms.pop(channel.id)
            temp_voice_channels.discard(channel.id)
            text_channel = member.guild.get_channel(room_data['text_channel_id'])
            try:
                await channel.delete(reason="Suppression voc temporaire vide")
            except:
                pass
            if text_channel:
                try:
                    await text_channel.delete(reason="Suppression panel voc temporaire")
                except:
                    pass

@bot.tree.command(name="welcome-set", description="Configurer le message de bienvenue pour les nouveaux membres")
@app_commands.describe(channel="Salon de bienvenue", message="Message ({user} sera remplacé par la mention)")
async def welcome_set(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    data['config']['welcome_channel'] = channel.id
    data['config']['welcome_message'] = message
    
    embed = discord.Embed(title="👋 Bienvenue Configuré", description=f"**Salon:** {channel.mention}\n**Message:** {message}\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)

# SONDAGES
@bot.tree.command(name="poll", description="Créer un sondage avec réactions automatiques")
@app_commands.describe(
    question="Question du sondage", 
    option1="Option 1", 
    option2="Option 2", 
    option3="Option 3 (optionnel)", 
    option4="Option 4 (optionnel)", 
    duration="Durée (ex: 30m, 2h, 1d - optionnel)"
)
async def poll(interaction: discord.Interaction, question: str, option1: str, option2: str, option3: str = None, option4: str = None, duration: str = None):
    options = [option1, option2]
    if option3: 
        options.append(option3)
    if option4: 
        options.append(option4)
    
    embed = discord.Embed(title="📊 SONDAGE", description=question, color=0xa30174)
    
    reactions = ['1️⃣', '2️⃣', '3️⃣', '4️⃣']
    for i, option in enumerate(options):
        embed.add_field(name=f"{reactions[i]} Option {i+1}", value=option, inline=False)
    
    if duration:
        embed.set_footer(text=f"Durée: {duration}")
    
    message = await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    
    for i in range(len(options)):
        await msg.add_reaction(reactions[i])

# SYSTÈME DE TICKETS
@bot.tree.command(name="viewpanelticket", description="Afficher le panneau avec menu déroulant pour ouvrir les tickets")
async def viewpanelticket(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    embed = create_ticket_embed(interaction.guild.id)
    view = TicketPanelView(interaction.guild.id)
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="custompanel", description="Personnaliser complètement l'embed du panneau ticket")
async def custompanel(interaction: discord.Interaction):
    # Rafraîchir les catégories avant d'ouvrir le modal
    refresh_ticket_categories(interaction.guild.id)
    await interaction.response.send_modal(CustomPanelModal())

@bot.tree.command(name="category", description="Modifier les catégories du menu des tickets")
@app_commands.describe(
    action="add/edit/remove", 
    nom="Nom de la catégorie", 
    nouveau_nom="Nouveau nom (pour add/edit)", 
    description="Description (pour add/edit)",
    emoji="Emoji pour la catégorie (pour add/edit)"
)
async def category(interaction: discord.Interaction, action: str, nom: str, nouveau_nom: str = None, description: str = None, emoji: str = None):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    categories = data['ticket_categories']
    
    if action == "add":
        if nom not in categories:
            display_name = nouveau_nom or nom
            # Ne pas ajouter l'emoji dans le nom, le garder séparément
            categories[nom] = {
                'name': display_name, 
                'description': description or 'Aucune description',
                'emoji': emoji or '🎫'
            }
            await interaction.response.send_message(f"✅ Catégorie `{nom}` ajoutée avec succès!")
        else:
            await interaction.response.send_message(f"❌ Catégorie `{nom}` existe déjà!", ephemeral=True)
    
    elif action == "edit":
        if nom in categories:
            if nouveau_nom: 
                categories[nom]['name'] = nouveau_nom
            if description: 
                categories[nom]['description'] = description
            if emoji:
                categories[nom]['emoji'] = emoji
            
            await interaction.response.send_message(f"✅ Catégorie `{nom}` modifiée avec succès!")
        else:
            await interaction.response.send_message(f"❌ Catégorie `{nom}` introuvable!", ephemeral=True)
    
    elif action == "remove":
        if nom in categories:
            del categories[nom]
            await interaction.response.send_message(f"✅ Catégorie `{nom}` supprimée avec succès!")
        else:
            await interaction.response.send_message(f"❌ Catégorie `{nom}` introuvable!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Action invalide! Utilisez: add, edit ou remove", ephemeral=True)

@bot.tree.command(name="setroleticket", description="Ajouter/retirer des rôles autorisés pour voir les tickets")
@app_commands.describe(role="Rôle à configurer", action="add/remove")
async def setroleticket(interaction: discord.Interaction, role: discord.Role, action: str):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    
    if action == "add":
        if role.id not in data['config']['ticket_roles']:
            data['config']['ticket_roles'].append(role.id)
            await interaction.response.send_message(f"✅ Rôle {role.mention} ajouté aux tickets!")
        else:
            await interaction.response.send_message(f"❌ Rôle déjà autorisé!", ephemeral=True)
    
    elif action == "remove":
        if role.id in data['config']['ticket_roles']:
            data['config']['ticket_roles'].remove(role.id)
            await interaction.response.send_message(f"✅ Rôle {role.mention} retiré des tickets!")
        else:
            await interaction.response.send_message(f"❌ Rôle pas dans la liste!", ephemeral=True)

@bot.tree.command(name="setcategory", description="Définir la catégorie Discord où les tickets seront créés")
@app_commands.describe(category="Catégorie Discord")
async def setcategory(interaction: discord.Interaction, category: discord.CategoryChannel):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    data['config']['ticket_category'] = category.id
    
    embed = discord.Embed(title="🎫 Catégorie Tickets Configurée", description=f"**Catégorie:** {category.name}\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="closeticket", description="Fermer un ticket (enlève les permissions d'écriture)")
async def closeticket(interaction: discord.Interaction):
    if not interaction.channel.name.startswith('ticket-'):
        await interaction.response.send_message("❌ Cette commande ne fonctionne que dans un ticket!", ephemeral=True)
        return
    
    # Extraire le numéro de ticket du nom si possible
    channel_parts = interaction.channel.name.split('-')
    ticket_info = f"Ticket {interaction.channel.name}"
    
    if len(channel_parts) >= 4:
        ticket_number = channel_parts[-1]  # Dernier élément = numéro
        category_name = channel_parts[1]   # Deuxième élément = catégorie
        ticket_info = f"Ticket #{ticket_number} ({category_name})"
    
    embed = discord.Embed(
        title="🔒 Ticket Fermé", 
        description=f"{ticket_info} fermé par {interaction.user.mention}", 
        color=0xff0000
    )
    embed.add_field(
        name="📋 Actions disponibles",
        value="• **Fermer définitivement:** `/deleteticket`\n• **Rouvrir:** Demander à un modérateur",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)
    
    # Retirer permissions d'écriture à tous les membres (sauf staff)
    overwrites = interaction.channel.overwrites
    for target, overwrite in overwrites.items():
        if isinstance(target, discord.Member) and not any(role.id in get_guild_data(interaction.guild.id)['config']['ticket_roles'] for role in target.roles):
            overwrite.send_messages = False
            await interaction.channel.set_permissions(target, overwrite=overwrite)
    
    # Log de fermeture
    await log_action(interaction.guild, "TICKET_CLOSE", interaction.channel, interaction.user, f"{ticket_info} fermé")
    
@bot.tree.command(name="synctickets", description="Forcer la synchronisation des catégories de tickets")
async def synctickets(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    """Commande pour forcer la synchronisation des catégories de tickets"""
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    
@bot.tree.command(name="configticket", description="Voir la configuration actuelle de l'embed tickets")
async def configticket(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    embed_config = data['config']['ticket_embed']
    categories = data['ticket_categories']
    
    # Embed de configuration
    config_embed = discord.Embed(
        title="⚙️ Configuration Tickets",
        color=0x0099ff
    )
    
    # Configuration embed
    config_embed.add_field(
        name="📝 Embed Actuel",
        value=f"**Titre:** {embed_config['title']}\n**Description:** {embed_config['description'][:100]}{'...' if len(embed_config['description']) > 100 else ''}\n**Couleur:** {embed_config['color']}\n**Image:** {'✅' if embed_config['image_url'] else '❌'}\n**Thumbnail:** {'✅' if embed_config['thumbnail_url'] else '❌'}",
        inline=False
    )
    
    # Catégories
    category_list = []
    for key, cat_data in categories.items():
        emoji = cat_data.get('emoji', '🎫')
        name = cat_data.get('name', key)
        category_list.append(f"{emoji} **{name}**")
    
    config_embed.add_field(
        name="🎯 Catégories",
        value="\n".join(category_list),
        inline=False
    )
    
    # Configuration système
    ticket_category_ch = data['config'].get('ticket_category')
    ticket_logs_ch = data['config'].get('ticket_logs_channel')
    
    system_info = f"**Catégorie Discord:** {f'<#{ticket_category_ch}>' if ticket_category_ch else '❌ Non configuré'}\n"
    system_info += f"**Salon de logs:** {f'<#{ticket_logs_ch}>' if ticket_logs_ch else '❌ Non configuré'}\n"
    system_info += f"**Rôles autorisés:** {len(data['config']['ticket_roles'])}\n"
    system_info += f"**Total créés:** {data['ticket_counter']}"
    
    config_embed.add_field(
        name="⚙️ Système",
        value=system_info,
        inline=False
    )
    
    config_embed.add_field(
        name="💡 Commandes",
        value="`/custompanel` - Modifier l'embed\n`/category` - Modifier les catégories\n`/viewpanelticket` - Afficher le panel\n`/synctickets` - Forcer la synchronisation\n`/setticketlogs` - Configurer salon de logs\n`/ticketstats` - Voir les statistiques",
        inline=False
    )
    
    await interaction.response.send_message(embed=config_embed, ephemeral=True)

@bot.tree.command(name="resetticket", description="Remettre la configuration tickets par défaut")
async def resetticket(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    
    # Reset embed
    data['config']['ticket_embed'] = {
        'title': '🎫 Système de Tickets',
        'description': 'Sélectionnez une catégorie pour ouvrir un ticket:',
        'color': '#a30174',
        'image_url': None,
        'thumbnail_url': None
    }
    
    # Reset catégories
    data['ticket_categories'] = {
        'support': {'name': 'Support', 'description': 'Support technique', 'emoji': '🛠️'},
        'bug': {'name': 'Bug Report', 'description': 'Signaler un bug', 'emoji': '🐛'},
        'other': {'name': 'Autre', 'description': 'Autres demandes', 'emoji': '❓'}
    }
    
    embed = discord.Embed(
        title="🔄 Configuration Reset",
        description="La configuration des tickets a été remise par défaut !",
        color=0xa30174
    )
    embed.add_field(
        name="✅ Remis à zéro",
        value="• Embed par défaut\n• Catégories par défaut\n• Images supprimées",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="ticketstats", description="Voir les statistiques des tickets du serveur")
async def ticketstats(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    guild = interaction.guild
    
    # Compter les tickets ouverts
    open_tickets = len([ch for ch in guild.text_channels if ch.name.startswith('ticket-')])
    
    # Statistiques par catégorie
    category_stats = {}
    for channel in guild.text_channels:
        if channel.name.startswith('ticket-'):
            parts = channel.name.split('-')
            if len(parts) >= 4:
                category = parts[1]
                category_stats[category] = category_stats.get(category, 0) + 1
    
    embed = discord.Embed(
        title="📊 Statistiques des Tickets",
        color=0xa30174
    )
    
    embed.add_field(
        name="📈 Général",
        value=f"**Total créés:** {data['ticket_counter']}\n**Actuellement ouverts:** {open_tickets}",
        inline=True
    )
    
    if category_stats:
        stats_text = "\n".join([f"**{cat.title()}:** {count}" for cat, count in category_stats.items()])
        embed.add_field(
            name="📋 Par Catégorie (Ouverts)",
            value=stats_text,
            inline=True
        )
    
    # Informations système
    embed.add_field(
        name="⚙️ Configuration",
        value=f"**Catégories disponibles:** {len(data['ticket_categories'])}\n**Rôles autorisés:** {len(data['config']['ticket_roles'])}",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="presetticket", description="Charger un preset d'embed pour les tickets")
@app_commands.describe(preset="Choisir un preset : default, modern, elegant, gaming")
async def presetticket(interaction: discord.Interaction, preset: str):
    if not await check_permissions(interaction):
        return
        return
    
    data = get_guild_data(interaction.guild.id)
    
    presets = {
        'default': {
            'title': '🎫 Système de Tickets',
            'description': 'Sélectionnez une catégorie pour ouvrir un ticket:',
            'color': '#a30174',
            'image_url': None,
            'thumbnail_url': None
        },
        'modern': {
            'title': '💬 Support & Assistance',
            'description': '**Besoin d\'aide ?**\n\nNotre équipe est là pour vous accompagner. Sélectionnez la catégorie qui correspond le mieux à votre demande.',
            'color': '#5865f2',
            'image_url': None,
            'thumbnail_url': None
        },
        'elegant': {
            'title': '✨ Centre d\'Assistance',
            'description': '**Nous sommes ravis de vous aider !**\n\nPour une assistance rapide et personnalisée, veuillez choisir la catégorie appropriée ci-dessous.',
            'color': '#9b59b6',
            'image_url': None,
            'thumbnail_url': None
        },
        'gaming': {
            'title': '🎮 Support Gaming',
            'description': '**GG, vous avez besoin d\'aide !**\n\nNotre équipe de support est prête à vous aider. Choisissez votre catégorie pour commencer.',
            'color': '#e74c3c',
            'image_url': None,
            'thumbnail_url': None
        }
    }
    
    if preset.lower() not in presets:
        available_presets = ', '.join(presets.keys())
        await interaction.response.send_message(f"❌ Preset invalide! Presets disponibles: {available_presets}", ephemeral=True)
        return
    
    # Charger le preset
    data['config']['ticket_embed'] = presets[preset.lower()]
    
    # Afficher le résultat
    embed = create_ticket_embed(interaction.guild.id)
    view = TicketPanelView(interaction.guild.id)
    
    await interaction.response.send_message(
        content=f"✅ Preset **{preset}** chargé avec succès!",
        embed=embed, 
        view=view
    )

    # Rafraîchir les catégories
    categories = refresh_ticket_categories(interaction.guild.id)
    
    embed = discord.Embed(
        title="🔄 Synchronisation des Tickets",
        description="Catégories de tickets synchronisées avec succès !",
        color=0xa30174
    )
    
    # Lister les catégories actuelles
    category_list = []
    for key, cat_data in categories.items():
        emoji = cat_data.get('emoji', '🎫')
        name = cat_data.get('name', key)
        category_list.append(f"{emoji} **{name}** (`{key}`)")
    
    embed.add_field(
        name="📋 Catégories Disponibles",
        value="\n".join(category_list),
        inline=False
    )
    
    embed.add_field(
        name="💡 Info",
        value="Utilisez `/viewpanelticket` ou `/custompanel` pour voir les changements.",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

    # Retirer permissions d'écriture
    overwrites = interaction.channel.overwrites
    for target, overwrite in overwrites.items():
        if isinstance(target, discord.Member):
            overwrite.send_messages = False
            await interaction.channel.set_permissions(target, overwrite=overwrite)

@bot.tree.command(name="setticketlogs", description="Définir le salon de logs pour les tickets")
@app_commands.describe(channel="Salon où seront envoyés les logs des tickets")
async def setticketlogs(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("❌ Permissions insuffisantes!", ephemeral=True)
        return
    
    data = get_guild_data(interaction.guild.id)
    data['config']['ticket_logs_channel'] = channel.id
    
    embed = discord.Embed(
        title="📋 Salon de Logs Tickets Configuré",
        description=f"**Salon:** {channel.mention}\n**Par:** {interaction.user.mention}",
        color=0xa30174
    )
    embed.add_field(
        name="ℹ️ Information",
        value="Les transcripts des tickets supprimés seront envoyés dans ce salon sous forme de fichier .txt",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="removeticketlogs", description="Supprimer le salon de logs tickets")
async def removeticketlogs(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    data['config']['ticket_logs_channel'] = None
    
    embed = discord.Embed(
        title="📋 Salon de Logs Tickets Supprimé",
        description=f"**Par:** {interaction.user.mention}",
        color=0xff0000
    )
    embed.add_field(
        name="ℹ️ Information",
        value="Les logs des tickets ne seront plus envoyés dans un salon (seulement en DM aux créateurs)",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="deleteticket", description="Supprimer complètement le salon ticket (avec transcript)")
async def deleteticket(interaction: discord.Interaction):
    # Vérifier que c'est bien un ticket
    if not interaction.channel.name.startswith('ticket-'):
        await interaction.response.send_message("❌ Cette commande ne fonctionne que dans un ticket!", ephemeral=True)
        return
    
    # Message de confirmation
    await interaction.response.send_message(
        "🗑️ **Génération du transcript et suppression du ticket dans 5 secondes...**\n"
        "📄 Le transcript sera envoyé en DM et dans le salon de logs."
    )
    await asyncio.sleep(5)
    
    print(f"\n[LOGS] Début suppression ticket: {interaction.channel.name}")
    
    # ÉTAPE 1: Créer le transcript complet
    print("[LOGS] Création du transcript...")
    transcript_text = await create_ticket_transcript(interaction.channel)
    print(f"[LOGS] Transcript créé")
    
    # ÉTAPE 2: Extraire les informations du ticket
    print("[LOGS] ÉTAPE 2: Extraction des informations...")
    channel_parts = interaction.channel.name.split('-')
    ticket_number = "N/A"
    ticket_category = "N/A"
    creator_user = None
    
    print(f"[LOGS] Channel name: {interaction.channel.name}")
    print(f"[LOGS] Channel topic: {interaction.channel.topic}")
    print(f"[LOGS] Parties du nom: {channel_parts}")
    
    # MÉTHODE 1: Extraire l'ID utilisateur depuis le topic (PLUS FIABLE)
    if interaction.channel.topic:
        try:
            # Le topic contient "ID: 123456789"
            if "ID:" in interaction.channel.topic:
                user_id_str = interaction.channel.topic.split("ID:")[1].split(")")[0].strip()
                user_id = int(user_id_str)
                creator_user = interaction.guild.get_member(user_id)
                if creator_user:
                    print(f"[LOGS] ✅ Créateur trouvé via TOPIC: {creator_user.name} (ID: {creator_user.id})")
        except Exception as e:
            print(f"[LOGS] ⚠️ Erreur extraction ID depuis topic: {e}")
    
    # Format: ticket-category-username-number
    if len(channel_parts) >= 4:
        ticket_category = channel_parts[1].title()
        ticket_number = channel_parts[-1]
        username_part = channel_parts[2]
        
        print(f"[LOGS] ✅ Catégorie: {ticket_category}")
        print(f"[LOGS] ✅ Numéro: {ticket_number}")
        
        # MÉTHODE 2: Chercher par nom nettoyé (FALLBACK si topic vide)
        if not creator_user:
            print(f"[LOGS] Recherche du créateur par nom...")
            for member in interaction.guild.members:
                try:
                    clean_member = clean_username(member.display_name)
                    if clean_member == username_part:
                        creator_user = member
                        print(f"[LOGS] ✅ Créateur trouvé via NOM: {member.name}")
                        break
                except:
                    continue
    
    if not creator_user:
        print(f"[LOGS] ⚠️ Créateur non trouvé")
    
    # ÉTAPE 3: Préparer les informations du ticket
    ticket_info = {
        'number': ticket_number,
        'category': ticket_category,
        'creator': creator_user.mention if creator_user else "Inconnu"
    }
    
    # ÉTAPE 4: Envoyer les logs via la fonction dédiée
    print("[LOGS] Envoi des logs...")
    await send_ticket_log(
        interaction.guild,
        interaction.channel.name,
        ticket_info,
        transcript_text,
        interaction.user
    )
    
    # ÉTAPE 5: Envoyer en DM au créateur
    print("[LOGS] Envoi en DM au créateur...")
    if creator_user:
        try:
            dm_embed = discord.Embed(
                title="📄 Transcript de votre ticket",
                color=0xa30174,
                timestamp=datetime.now()
            )
            dm_embed.add_field(name="🎫 Ticket", value=interaction.channel.name, inline=True)
            dm_embed.add_field(name="📊 Numéro", value=f"#{ticket_number}", inline=True)
            dm_embed.add_field(name="🏷️ Catégorie", value=ticket_category, inline=True)
            dm_embed.add_field(name="🗑️ Supprimé par", value=f"{interaction.user.name}", inline=True)
            dm_embed.set_footer(text=f"Serveur: {interaction.guild.name}")
            
            filename = f"transcript-{interaction.channel.name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
            file_for_dm = discord.File(io.StringIO(transcript_text), filename=filename)
            await creator_user.send(embed=dm_embed, file=file_for_dm)
            print(f"[LOGS] ✅ DM envoyé à {creator_user.name}")
        except discord.Forbidden:
            print(f"[LOGS] ❌ DM fermés pour {creator_user.name}")
        except Exception as e:
            print(f"[LOGS] ❌ Erreur envoi DM: {e}")
    else:
        print("[LOGS] ❌ Créateur non trouvé, impossible d'envoyer le DM")
    
    # ÉTAPE 6: Supprimer le salon
    print("[LOGS] Suppression du salon...")
    try:
        await interaction.channel.delete(reason=f"Ticket supprimé par {interaction.user}")
        print("[LOGS] ✅ Salon supprimé avec succès")
    except Exception as e:
        print(f"[LOGS] ❌ Erreur suppression salon: {e}")

@bot.tree.command(name="openticket", description="Ouvrir un ticket manuellement pour un membre")
@app_commands.describe(member="Membre pour qui ouvrir le ticket", category="Catégorie du ticket")
async def openticket(interaction: discord.Interaction, member: discord.Member, category: str):
    if not await check_permissions(interaction):
        return
    data = get_guild_data(interaction.guild.id)
    
    if category not in data['ticket_categories']:
        await interaction.response.send_message("❌ Catégorie inexistante!", ephemeral=True)
        return
    
    await create_ticket(interaction, member, category)

@bot.tree.command(name="ticket-create", description="Créer un ticket dans une catégorie spécifique")
@app_commands.describe(category="Catégorie du ticket")
async def ticket_create(interaction: discord.Interaction, category: str):
    await create_ticket(interaction, interaction.user, category)

@bot.tree.command(name="testlogs", description="[ADMIN] Tester le système de logs tickets")
async def testlogs(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Seuls les administrateurs peuvent utiliser cette commande!", ephemeral=True)
        return
    
    data = get_guild_data(interaction.guild.id)
    log_channel_id = data['config']['ticket_logs_channel']
    
    embed = discord.Embed(
        title="🔍 Test du Système de Logs",
        color=0x0099ff
    )
    
    # Vérifier la configuration
    if not log_channel_id:
        embed.add_field(
            name="❌ Salon de logs",
            value="Aucun salon de logs configuré!\nUtilisez `/setticketlogs`",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    log_channel = interaction.guild.get_channel(log_channel_id)
    
    if not log_channel:
        embed.add_field(
            name="❌ Salon introuvable",
            value=f"Le salon avec l'ID {log_channel_id} n'existe plus!\nReconfigurez avec `/setticketlogs`",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Tester l'envoi dans le salon de logs
    embed.add_field(
        name="✅ Configuration",
        value=f"**Salon de logs:** {log_channel.mention}",
        inline=False
    )
    
    # Envoyer un message de test
    try:
        test_embed = discord.Embed(
            title="🧪 Test du Système de Logs",
            description="Ceci est un message de test pour vérifier que les logs fonctionnent correctement.",
            color=0x00ff00,
            timestamp=datetime.now()
        )
        test_embed.add_field(name="Testé par", value=interaction.user.mention, inline=True)
        test_embed.set_footer(text="Test réussi!")
        
        # Créer un fichier de test
        test_content = f"=== TEST DU SYSTÈME DE LOGS ===\n"
        test_content += f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        test_content += f"Testé par: {interaction.user.name}#{interaction.user.discriminator}\n"
        test_content += f"Serveur: {interaction.guild.name}\n"
        test_content += f"\nCe fichier de test confirme que le système de logs fonctionne correctement.\n"
        
        test_file = discord.File(
            io.StringIO(test_content),
            filename=f"test-logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        )
        
        await log_channel.send(embed=test_embed, file=test_file)
        
        embed.add_field(
            name="✅ Test réussi",
            value=f"Un message de test a été envoyé dans {log_channel.mention}",
            inline=False
        )
        embed.color = 0x00ff00
        
    except discord.Forbidden:
        embed.add_field(
            name="❌ Permissions manquantes",
            value=f"Le bot n'a pas la permission d'envoyer des messages dans {log_channel.mention}",
            inline=False
        )
        embed.color = 0xff0000
        
    except Exception as e:
        embed.add_field(
            name="❌ Erreur",
            value=f"```{str(e)}```",
            inline=False
        )
        embed.color = 0xff0000
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def create_ticket(interaction, user, category_key):
    """Fonction pour créer un ticket avec nommage amélioré"""
    data = get_guild_data(interaction.guild.id)
    guild = interaction.guild
    
    # Obtenir le prochain numéro de ticket
    ticket_number = get_next_ticket_number(interaction.guild.id)
    
    # Obtenir le nom d'affichage de la catégorie
    category_display_name = get_category_display_name(interaction.guild.id, category_key)
    
    # Nettoyer les noms pour le salon
    clean_category = clean_category_name(category_display_name)
    clean_user = clean_username(user.display_name)
    
    # Créer le nom du salon avec le nouveau format
    channel_name = f"ticket-{clean_category}-{clean_user}-{ticket_number}"
    
    # Vérifier si l'utilisateur a déjà un ticket ouvert
    for channel in guild.text_channels:
        if channel.name.startswith(f"ticket-") and f"-{clean_user}-" in channel.name:
            await interaction.response.send_message("❌ Vous avez déjà un ticket ouvert!", ephemeral=True)
            return
    
    # Créer les permissions du salon ticket
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    
    # Ajouter les rôles autorisés
    for role_id in data['config']['ticket_roles']:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    
    # Obtenir la catégorie Discord pour les tickets
    ticket_category = None
    if data['config']['ticket_category']:
        ticket_category = guild.get_channel(data['config']['ticket_category'])

        # Créer le salon ticket
    try:
        # IMPORTANT: Ajouter l'ID utilisateur dans le topic pour pouvoir le retrouver facilement
        topic_text = f"Ticket #{ticket_number} de {user.display_name} (ID: {user.id}) - Catégorie: {category_display_name}"
        
        channel = await guild.create_text_channel(
            channel_name,
            overwrites=overwrites,
            category=ticket_category,
            topic=topic_text
        )
    except Exception as e:
        # Si le nom est trop long ou invalide, utiliser un nom de fallback
        fallback_name = f"ticket-{user.id}-{ticket_number}"
        topic_text = f"Ticket #{ticket_number} de {user.display_name} (ID: {user.id}) - Catégorie: {category_display_name}"
        
        channel = await guild.create_text_channel(
            fallback_name,
            overwrites=overwrites,
            category=ticket_category,
            topic=topic_text
        )
    
    # Créer l'embed du nouveau ticket
    embed = discord.Embed(
        title="🎫 Nouveau Ticket",
        description=f"**Utilisateur:** {user.mention}\n**Catégorie:** {category_display_name}\n**Ticket #:** {ticket_number}\n**Date:** <t:{int(datetime.now().timestamp())}:F>",
        color=0xa30174
    )
    
    # Ajouter des informations supplémentaires
    embed.add_field(
        name="📋 Informations",
        value=f"**ID Utilisateur:** {user.id}\n**Nom du salon:** {channel.name}",
        inline=False
    )
    
    # Envoyer le message initial dans le ticket
    await channel.send(f"{user.mention} **|** Ticket #{ticket_number}", embed=embed, view=TicketControlView())
    
    # Log de création du ticket
    await log_action(guild, "TICKET_CREATE", channel, user, f"Ticket #{ticket_number} créé - Catégorie: {category_display_name}")
    
    # Enregistrer l'activité initiale du ticket
    update_ticket_activity(guild.id, channel.id, user.id)

   # Répondre à l'interaction
    try:
        # Vérifier si l'interaction n'a pas déjà reçu de réponse
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"✅ Ticket **#{ticket_number}** créé: {channel.mention}\n📝 Catégorie: **{category_display_name}**",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"✅ Ticket **#{ticket_number}** créé: {channel.mention}\n📝 Catégorie: **{category_display_name}**",
                ephemeral=True
            )
    except Exception as e:
        print(f"Erreur lors de l’envoi du message : {e}")

# KEY PROMOTEUR
@bot.tree.command(name="viewpanelkeypromot", description="Afficher le panel pour récupérer des clés promoteur")
async def viewpanelkeypromot(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    embed = create_key_embed(interaction.guild.id)
    data = get_guild_data(interaction.guild.id)
    button_label = data['config']['key_embed'].get('button_label', 'Récupérer Clé')
    view = KeyPromotView(button_label)
    await interaction.response.send_message(embed=embed, view=view)



@bot.tree.command(name="custompanelkey", description="Personnaliser l'embed du panel key promoteur")
async def custompanelkey(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    await interaction.response.send_modal(CustomKeyPanelModal())

@bot.tree.command(name="addkey", description="Ajouter une ou plusieurs clés au stock (séparées par des espaces)")
@app_commands.describe(keys="Clés à ajouter (séparées par des espaces)")
async def addkey(interaction: discord.Interaction, keys: str):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    key_list = keys.split()
    
    added_keys = []
    existing_keys = []
    
    for key in key_list:
        if key not in data['keys']:
            data['keys'].append(key)
            added_keys.append(key)
        else:
            existing_keys.append(key)
    
    response_parts = []
    if added_keys:
        response_parts.append(f"✅ {len(added_keys)} clé(s) ajoutée(s): {', '.join(f'`{k}`' for k in added_keys)}")
    if existing_keys:
        response_parts.append(f"❌ {len(existing_keys)} clé(s) déjà existante(s): {', '.join(f'`{k}`' for k in existing_keys)}")
    
    response_parts.append(f"📊 Stock total: {len(data['keys'])} clés")
    
    await interaction.response.send_message("\n".join(response_parts), ephemeral=True)

@bot.tree.command(name="removekey", description="Supprimer une clé du stock")
@app_commands.describe(key="Clé à supprimer")
async def removekey(interaction: discord.Interaction, key: str):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    if key in data['keys']:
        data['keys'].remove(key)
        await interaction.response.send_message(f"✅ Clé `{key}` supprimée! Stock: {len(data['keys'])}", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Clé `{key}` introuvable!", ephemeral=True)

@bot.tree.command(name="stockkey", description="Voir le nombre de clés disponibles")
async def stockkey(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    
    embed = discord.Embed(title="📊 Stock Clés Promoteur", description=f"**Clés disponibles:** {len(data['keys'])}", color=0x0099ff)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="setrolekey", description="Définir les rôles autorisés à récupérer des clés")
@app_commands.describe(role="Rôle à autoriser")
async def setrolekey(interaction: discord.Interaction, role: discord.Role):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    if role.id not in data['config']['key_roles']:
        data['config']['key_roles'].append(role.id)
        await interaction.response.send_message(f"✅ Rôle {role.mention} autorisé pour les clés!")
    else:
        await interaction.response.send_message(f"❌ Rôle déjà autorisé!", ephemeral=True)

@bot.tree.command(name="setcooldownkey", description="Définir le cooldown entre les récupérations de clés")
@app_commands.describe(minutes="Cooldown en minutes")
async def setcooldownkey(interaction: discord.Interaction, minutes: int):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    data['config']['key_cooldown'] = minutes
    
    embed = discord.Embed(title="⏰ Cooldown Clés Configuré", description=f"**Cooldown:** {minutes} minutes\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)



# COMMANDE /HELP avec menu déroulant (à ajouter après les autres commandes)
@bot.tree.command(name="help", description="Afficher toutes les commandes disponibles par catégorie")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📚 Menu d'Aide - Bot Discord",
        description="Sélectionnez une catégorie dans le menu déroulant pour voir les commandes disponibles.\n\n**Total : 90 commandes réparties en 13 catégories**",
        color=0xa30174
    )
    embed.add_field(name="🎯 Fonctionnalités", value="• Giveaways interactifs avec images\n• Modération avancée complète\n• Panels permanents (jamais d'expiration)\n• Auto-modération intelligente", inline=True)
    embed.add_field(name="🔧 Outils Avancés", value="• Système de tickets avec logs\n• Gestion des clés avec cooldown\n• Messages sticky repositionnables\n• Statistiques complètes en temps réel", inline=True)
    embed.add_field(name="✨ Nouveautés", value="• Embeds avec images et URLs cliquables\n• Boutons personnalisables\n• Catégories tickets avec emojis\n• **Logs tickets automatiques**", inline=True)
    embed.add_field(name="ℹ️ Information", value="• **90 commandes** synchronisées\n• **13 catégories** disponibles\n• **4 panels permanents** (Tickets, Keys, Free Keys, Help)\n• Support complet et assistance\n• **Bot créé par TEKAZ **", inline=False)
    
    embed.set_footer(text="🚀 Panel d'aide permanent - Ne s'arrête jamais automatiquement !")
    embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user.display_avatar else None)
    
    view = HelpView()
    await interaction.response.send_message(embed=embed, view=view)

# VIEW POUR LE MENU D'AIDE
class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # 5 minutes de timeout
        self.add_item(HelpSelect())

class HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="🎉 Giveaways", 
                description="5 commandes - Créer et gérer des giveaways", 
                value="giveaways",
                emoji="🎉"
            ),
            discord.SelectOption(
                label="🔨 Modération", 
                description="15 commandes - Ban, kick, mute, warn, clear...", 
                value="moderation",
                emoji="🔨"
            ),
            discord.SelectOption(
                label="🛡️ Auto-Modération", 
                description="12 commandes - Anti-lien, anti-spam, mots interdits", 
                value="automod",
                emoji="🛡️"
            ),
            discord.SelectOption(
                label="🎭 Gestion Rôles", 
                description="4 commandes - Autorôle, ajouter/retirer rôles", 
                value="roles",
                emoji="🎭"
            ),
            discord.SelectOption(
                label="ℹ️ Informations", 
                description="4 commandes - Userinfo, serverinfo, warnings", 
                value="info",
                emoji="ℹ️"
            ),
            discord.SelectOption(
                label="⚙️ Configuration", 
                description="6 commandes - Config générale, logs, permissions", 
                value="config",
                emoji="⚙️"
            ),
            discord.SelectOption(
                label="🎯 Système Vouchs", 
                description="5 commandes - Avis clients personnalisables", 
                value="vouchs",
                emoji="🎯"
            ),
            discord.SelectOption(
                label="🔊 Salons Vocaux", 
                description="2 commandes - Vocaux temporaires, bienvenue", 
                value="voice",
                emoji="🔊"
            ),
            discord.SelectOption(
                label="📊 Sondages", 
                description="1 commande - Créer des sondages interactifs", 
                value="polls",
                emoji="📊"
            ),
            discord.SelectOption(
                label="🎫 Système Tickets", 
                description="16 commandes - Tickets avec logs automatiques", 
                value="tickets",
                emoji="🎫"
            ),
            discord.SelectOption(
                label="🔑 Key Promoteur", 
                description="9 commandes - Système de clés avec cooldown", 
                value="keys",
                emoji="🔑"
            ),
            discord.SelectOption(
                label="🔓 Free Key", 
                description="8 commandes - Clés gratuites personnalisables", 
                value="freekeys",
                emoji="🔓"
            ),
            discord.SelectOption(
                label="📌 Sticky Messages", 
                description="6 commandes - Messages qui restent en bas", 
                value="sticky",
                emoji="📌"
            ),
            discord.SelectOption(
            label="⏰ Inactivité Tickets", 
            description="6 commandes - Gestion automatique inactivité", 
            value="inactivity",
            emoji="⏰"
            )
        ]
        super().__init__(placeholder="Choisissez une catégorie...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        
        embeds = {
            "giveaways": discord.Embed(
                title="🎉 Commandes Giveaways",
                description="**5 commandes disponibles**",
                color=0xff69b4
            ).add_field(name="Commandes", value="""
`/gcreate` - Créer un giveaway avec panneau interactif (+ image)
`/greroll <message_id>` - Relancer un giveaway
`/glist` - Lister les giveaways actifs
`/gend <message_id>` - Terminer un giveaway prématurément
`/gdelete <message_id>` - Supprimer un giveaway
            """, inline=False),
            
            "moderation": discord.Embed(
                title="🔨 Commandes Modération",
                description="**15 commandes disponibles**",
                color=0xff0000
            ).add_field(name="Commandes", value="""
`/ban <member> [reason]` - Bannir un membre
`/kick <member> [reason]` - Exclure un membre
`/mute <member> <duration> [reason]` - Rendre muet un membre
`/unmute <member>` - Enlever le mute
`/unban <user_id>` - Débannir un utilisateur
`/clear <amount>` - Supprimer des messages
`/warn <member> <reason>` - Donner un avertissement
`/clearwarnings <member>` - Effacer les avertissements
`/nuke` - Supprimer tous les messages (recrée le salon)
`/locksalon [channel]` - Verrouiller un salon
`/unlocksalon [channel]` - Déverrouiller un salon
`/embed` - Créer un embed avancé (+ images, URL cliquable)
`/slowmode <seconds> [channel]` - Configurer le mode lent
`/removeslowmode [channel]` - Supprimer le mode lent
`/massban <reason>` - Bannissement en masse (fichier .txt requis)
            """, inline=False),
            
            "automod": discord.Embed(
                title="🛡️ Commandes Auto-Modération",
                description="**12 commandes disponibles**",
                color=0x00bfff
            ).add_field(name="Commandes", value="""
`/automod <status>` - Configurer l'auto-modération générale
`/antilink_config <status> <action>` - Configurer l'anti-lien
`/antispam_config <status> <action>` - Configurer l'anti-spam
`/antiraid_config <status> <action>` - Configurer l'anti-raid
`/antilink <status>` - Activer/désactiver l'anti-lien
`/antilinkaction <action>` - Configurer l'action anti-lien
`/whitelist_add <domain>` - Ajouter un domaine autorisé
`/whitelist_remove <domain>` - Retirer un domaine autorisé
`/whitelist_list` - Voir la liste blanche des domaines
`/badwordaction <action>` - Configurer l'action pour les mots interdits
`/addword <word>` - Ajouter un mot interdit
`/removeword <word>` - Retirer un mot interdit
            """, inline=False),
            
            "roles": discord.Embed(
                title="🎭 Commandes Gestion Rôles",
                description="**4 commandes disponibles**",
                color=0x9932cc
            ).add_field(name="Commandes", value="""
`/autorole <role>` - Configurer le rôle automatique pour les nouveaux membres
`/autorole_remove` - Supprimer l'autorôle
`/addrole <member> <role>` - Ajouter un rôle à un membre
`/removerole <member> <role>` - Retirer un rôle à un membre
            """, inline=False),
            
            "info": discord.Embed(
                title="ℹ️ Commandes Informations",
                description="**4 commandes disponibles**",
                color=0x00ff7f
            ).add_field(name="Commandes", value="""
`/userinfo [member]` - Afficher les informations d'un utilisateur
`/serverinfo` - Afficher les informations du serveur
`/warnings [member]` - Voir les avertissements d'un membre
`/listwords` - Voir la liste des mots interdits (en privé)
            """, inline=False),
            
            "config": discord.Embed(
                title="⚙️ Commandes Configuration",
                description="**6 commandes disponibles**",
                color=0x1e90ff
            ).add_field(name="Commandes", value="""
`/config` - Voir la configuration complète du bot
`/setlogs <channel>` - Configurer le salon de logs
`/setlogs_remove` - Supprimer le salon de logs
`/setrole <role>` - Configurer les rôles autorisés à utiliser le bot
`/unsetroles <role>` - Retirer un rôle des autorisations
`/help` - Afficher ce menu d'aide
            """, inline=False),
            
            "vouchs": discord.Embed(
                title="🎯 Commandes Système Vouchs",
                description="**5 commandes disponibles**",
                color=0xff6347
            ).add_field(name="Commandes", value="""
`/vouch` - Laisser un avis client avec formulaire interactif (+ image)
`/modifembed <titre> <couleur> <footer> <thumbnail>` - Personnaliser l'apparence des embeds
`/resetcount` - Remettre le compteur de vouchs à zéro
`/configembed` - Voir la configuration actuelle des vouchs
`/stats` - Afficher les statistiques complètes du serveur
            """, inline=False),
            
            "voice": discord.Embed(
                title="🔊 Commandes Salons Vocaux",
                description="**2 commandes disponibles**",
                color=0x32cd32
            ).add_field(name="Commandes", value="""
`/tempvoice <nom> [max_users]` - Créer un salon vocal temporaire
`/welcome-set <channel> <message>` - Configurer le message de bienvenue
            """, inline=False),
            
            "polls": discord.Embed(
                title="📊 Commandes Sondages",
                description="**1 commande disponible**",
                color=0xffd700
            ).add_field(name="Commandes", value="""
`/poll <question> <option1> <option2> [option3] [option4] [duration]` - Créer un sondage avec réactions automatiques
            """, inline=False),
            
            "tickets": discord.Embed(
                title="🎫 Commandes Système Tickets",
                description="**16 commandes disponibles**",
                color=0x8a2be2
            ).add_field(name="📋 Panels & Affichage", value="""
`/viewpanelticket` - Afficher le panneau avec menu déroulant (permanent)
`/custompanel` - Personnaliser l'embed du panneau (+ sauvegarde auto)
            """, inline=False).add_field(name="⚙️ Configuration & Gestion", value="""
`/category <action> <nom> [nouveau_nom] [description] [emoji]` - Modifier les catégories
`/setroleticket <role> <action>` - Ajouter/retirer des rôles autorisés
`/setcategory <category>` - Définir la catégorie Discord des tickets
`/configticket` - Voir la configuration actuelle complète
`/synctickets` - Forcer la synchronisation des catégories
`/resetticket` - Remettre la configuration par défaut
`/presetticket <preset>` - Charger un preset d'embed (default/modern/elegant/gaming)
`/ticketstats` - Voir les statistiques des tickets
            """, inline=False).add_field(name="📋 Logs & Transcripts", value="""
`/setticketlogs <channel>` - Définir le salon de logs tickets (transcripts .txt)
`/removeticketlogs` - Supprimer le salon de logs tickets
            """, inline=False).add_field(name="🎫 Actions dans les Tickets", value="""
`/closeticket` - Fermer un ticket (dans le salon ticket)
`/deleteticket` - Supprimer un ticket avec transcript (dans le salon ticket)
`/openticket <member> <category>` - Ouvrir un ticket manuellement
`/ticket-create <category>` - Créer un ticket dans une catégorie spécifique
            """, inline=False).add_field(
                name="✨ Système de Logs",
                value="• **Transcripts automatiques** en .txt\n• **Envoi dans salon de logs** configuré\n• **Envoi en DM** au créateur du ticket\n• **Format complet** : messages, embeds, fichiers",
                inline=False
            ),
            
            "keys": discord.Embed(
                title="🔑 Commandes Key Promoteur",
                description="**9 commandes disponibles**",
                color=0xff4500
            ).add_field(name="📋 Panels & Affichage", value="""
`/viewpanelkeypromot` - Afficher le panel pour récupérer des clés (permanent)
`/custompanelkey` - Personnaliser l'embed du panel key (+ images, bouton custom)
            """, inline=False).add_field(name="⚙️ Gestion & Configuration", value="""
`/addkey <keys>` - Ajouter clés (séparées par espaces : KEY1 KEY2 KEY3)
`/removekey <key>` - Supprimer une clé du stock
`/stockkey` - Voir le nombre de clés disponibles
`/setrolekey <role>` - Définir les rôles autorisés à récupérer des clés
`/setcooldownkey <minutes>` - Définir le cooldown entre les récupérations
`/configkey` - Voir la configuration actuelle du panel key
`/resetkeyconfig` - Remettre la configuration par défaut
            """, inline=False).add_field(
                name="✨ Synchronisation",
                value="• `/custompanelkey` **sauvegarde automatiquement**\n• `/viewpanelkeypromot` **utilise la config sauvegardée**\n• **Parfaite synchronisation** entre les deux commandes",
                inline=False
            ),
            
            "freekeys": discord.Embed(
                title="🔓 Commandes Free Key",
                description="**8 commandes disponibles**",
                color=0x00ff00
            ).add_field(name="📋 Panels & Affichage", value="""
`/viewpanelfreekey` - Afficher le panel pour récupérer des clés gratuites (permanent)
`/custompanelfreekey` - Personnaliser l'embed du panel (+ images, bouton custom)
            """, inline=False).add_field(name="⚙️ Gestion & Configuration", value="""
`/addfreekey <keys>` - Ajouter free keys (séparées par espaces : FREE1 FREE2 FREE3)
`/removefreekey <key>` - Supprimer une free key du stock
`/stockfreekey` - Voir le stock de free keys
`/resetfreekey` - Reset la liste des utilisateurs (permet de récupérer à nouveau)
`/configfreekey` - Voir la configuration actuelle du panel free key
`/resetfreekeyconfig` - Remettre la configuration par défaut
            """, inline=False).add_field(
                name="✨ Synchronisation",
                value="• `/custompanelfreekey` **sauvegarde automatiquement**\n• `/viewpanelfreekey` **utilise la config sauvegardée**\n• **Parfaite synchronisation** entre les deux commandes",
                inline=False
            ),

             "inactivity": discord.Embed(
                title="⏰ Commandes Inactivité Tickets",
                description="**6 commandes disponibles**",
                color=0xff9900
           ).add_field(name="Commandes", value="""
`/inactivity-enable <status>` - Activer/désactiver le système
`/inactivity-delay <hours>` - Définir le délai avant avertissement (défaut: 24h)
`/inactivity-notify-staff <status>` - Activer/désactiver notifications staff
`/inactivity-customize` - Personnaliser le message d'avertissement
`/inactivity-status` - Voir la configuration et l'état du système
`/inactivity-check` - [ADMIN] Forcer la vérification maintenant
            """, inline=False).add_field(
                name="✨ Fonctionnement",
                value="• Avertissement après **24h** d'inactivité (configurable)\n• 2 boutons : Garder Ouvert / Fermer\n• Fermeture auto après **48h** sans réponse\n• Rappel tous les **24h** si gardé ouvert\n• Logs et DM automatiques lors de la fermeture",
                inline=False
            ),
            
            "sticky": discord.Embed(
                title="📌 Commandes Sticky Messages",
                description="**6 commandes disponibles**",
                color=0xffa500
            ).add_field(name="Commandes", value="""
`/stick <message>` - Créer un message qui reste toujours en bas du salon
`/stickstop` - Arrêter temporairement le sticky dans le salon actuel
`/stickstart` - Redémarrer le sticky précédemment arrêté
`/stickdelete` - Supprimer complètement le sticky du salon
`/getsticks` - Voir tous les messages sticky du serveur
`/setnamestick <nom>` - Modifier le nom du bot affiché dans les messages sticky
            """, inline=False)

            }


        embed = embeds.get(category)
        if embed:
            embed.set_footer(text="💡 Panels permanents : Tickets, Keys, Free Keys, Help | Bot créé par TEKAZ")
            await interaction.response.edit_message(embed=embed, view=self.view)

# FREE KEY
@bot.tree.command(name="viewpanelfreekey", description="Afficher le panel pour récupérer des clés gratuites")
async def viewpanelfreekey(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    embed = create_freekey_embed(interaction.guild.id)
    data = get_guild_data(interaction.guild.id)
    button_label = data['config']['freekey_embed'].get('button_label', 'Récupérer Free Key')
    view = FreeKeyView(button_label)
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="configfreekey", description="Voir la configuration actuelle du panel free key")
async def configfreekey(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    embed_config = data['config']['freekey_embed']
    
    config_embed = discord.Embed(
        title="⚙️ Configuration Free Key",
        color=0x0099ff
    )
    
    config_embed.add_field(
        name="📝 Embed Actuel",
        value=f"**Titre:** {embed_config['title']}\n**Description:** {embed_config['description'][:100]}{'...' if len(embed_config['description']) > 100 else ''}\n**Couleur:** {embed_config['color']}\n**Bouton:** {embed_config['button_label']}\n**Image:** {'✅' if embed_config.get('image_url') else '❌'}",
        inline=False
    )
    
    config_embed.add_field(
        name="📊 Stock",
        value=f"**Free keys disponibles:** {len(data['free_keys'])}\n**Utilisateurs ayant récupéré:** {len(free_key_users.get(interaction.guild.id, set()))}",
        inline=False
    )
    
    config_embed.add_field(
        name="💡 Commandes",
        value="`/custompanelfreekey` - Modifier l'embed\n`/viewpanelfreekey` - Afficher le panel\n`/resetfreekey` - Reset les utilisateurs",
        inline=False
    )
    
    await interaction.response.send_message(embed=config_embed, ephemeral=True)

@bot.tree.command(name="resetfreekeyconfig", description="Remettre la configuration free key par défaut")
async def resetfreekeyconfig(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    
    # Reset embed
    data['config']['freekey_embed'] = {
        'title': '🆓 Free Keys',
        'description': 'Récupérez votre clé gratuite\n\nUne clé par utilisateur',
        'color': '#00ff00',
        'image_url': None,
        'button_label': 'Récupérer Free Key'
    }
    
    embed = discord.Embed(
        title="🔄 Configuration Reset",
        description="La configuration des free keys a été remise par défaut !",
        color=0x00ff00
    )
    embed.add_field(
        name="✅ Remis à zéro",
        value="• Embed par défaut\n• Bouton par défaut\n• Image supprimée",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="custompanelfreekey", description="Personnaliser l'embed du panel free key")
async def custompanelfreekey(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    await interaction.response.send_modal(CustomFreeKeyPanelModal())

@bot.tree.command(name="addfreekey", description="Ajouter une ou plusieurs free keys au stock (séparées par des espaces)")
@app_commands.describe(keys="Free keys à ajouter (séparées par des espaces)")
async def addfreekey(interaction: discord.Interaction, keys: str):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    key_list = keys.split()
    
    added_keys = []
    existing_keys = []
    
    for key in key_list:
        if key not in data['free_keys']:
            data['free_keys'].append(key)
            added_keys.append(key)
        else:
            existing_keys.append(key)
    
    response_parts = []
    if added_keys:
        response_parts.append(f"✅ {len(added_keys)} free key(s) ajoutée(s): {', '.join(f'`{k}`' for k in added_keys)}")
    if existing_keys:
        response_parts.append(f"❌ {len(existing_keys)} free key(s) déjà existante(s): {', '.join(f'`{k}`' for k in existing_keys)}")
    
    response_parts.append(f"📊 Stock total: {len(data['free_keys'])} free keys")
    
    await interaction.response.send_message("\n".join(response_parts), ephemeral=True)


@bot.tree.command(name="removefreekey", description="Supprimer une free key du stock")
@app_commands.describe(key="Free key à supprimer")
async def removefreekey(interaction: discord.Interaction, key: str):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    if key in data['free_keys']:
        data['free_keys'].remove(key)
        await interaction.response.send_message(f"✅ Free key `{key}` supprimée! Stock: {len(data['free_keys'])}", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Free key `{key}` introuvable!", ephemeral=True)

@bot.tree.command(name="stockfreekey", description="Voir le stock de free keys")
async def stockfreekey(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    data = get_guild_data(interaction.guild.id)
    
    embed = discord.Embed(title="📊 Stock Free Keys", description=f"**Free keys disponibles:** {len(data['free_keys'])}", color=0xa30174)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="resetfreekey", description="Reset la liste des utilisateurs ayant déjà pris une free key")
async def resetfreekey(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    
    guild_id = interaction.guild.id
    if guild_id in free_key_users:
        free_key_users[guild_id] = set()
    
    embed = discord.Embed(title="🔄 Reset Free Keys", description="Tous les utilisateurs peuvent maintenant récupérer une nouvelle free key!", color=0xa30174)
    await interaction.response.send_message(embed=embed)

# STICKY MESSAGES
@bot.tree.command(name="stick", description="Créer un message qui reste toujours en bas du salon")
@app_commands.describe(message="Message qui restera collé en bas")
async def stick(interaction: discord.Interaction, message: str):
    if not await check_permissions(interaction):
        return
    guild_id = interaction.guild.id
    channel_id = interaction.channel.id
    
    if guild_id not in sticky_messages:
        sticky_messages[guild_id] = {}
    
    # Supprimer ancien sticky s'il existe
    if channel_id in sticky_messages[guild_id]:
        try:
            old_msg_id = sticky_messages[guild_id][channel_id]['message_id']
            if old_msg_id:
                old_message = await interaction.channel.fetch_message(old_msg_id)
                await old_message.delete()
        except:
            pass
    
    # Créer nouveau sticky
    embed = discord.Embed(description=message, color=0xa30174)
    embed.set_author(name=bot.user.display_name, icon_url=bot.user.display_avatar.url)
    
    msg = await interaction.response.send_message(embed=embed)
    response_msg = await interaction.original_response()
    
    sticky_messages[guild_id][channel_id] = {
        'content': message,
        'message_id': response_msg.id,
        'active': True,
        'bot_name': bot.user.display_name
    }
    
    await interaction.followup.send("✅ Message sticky créé!", ephemeral=True)

@bot.tree.command(name="stickstop", description="Arrêter temporairement le sticky dans le salon actuel")
async def stickstop(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    guild_id = interaction.guild.id
    channel_id = interaction.channel.id
    
    if guild_id in sticky_messages and channel_id in sticky_messages[guild_id]:
        sticky_messages[guild_id][channel_id]['active'] = False
        await interaction.response.send_message("⏸️ Sticky message mis en pause!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Aucun sticky message dans ce salon!", ephemeral=True)

@bot.tree.command(name="stickdelete", description="Supprimer complètement le sticky du salon")
async def stickdelete(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return
    guild_id = interaction.guild.id
    channel_id = interaction.channel.id
    
    if guild_id in sticky_messages and channel_id in sticky_messages[guild_id]:
        try:
            msg_id = sticky_messages[guild_id][channel_id]['message_id']
            if msg_id:
                message = await interaction.channel.fetch_message(msg_id)
                await message.delete()
        except:
            pass
        
        del sticky_messages[guild_id][channel_id]
        await interaction.response.send_message("🗑️ Sticky message supprimé!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Aucun sticky message dans ce salon!", ephemeral=True)

@bot.tree.command(name="setnamestick", description="Modifier le nom du bot affiché dans les messages sticky")
@app_commands.describe(nom="Nouveau nom à afficher")
async def setnamestick(interaction: discord.Interaction, nom: str):
    if not await check_permissions(interaction):
        return
    guild_id = interaction.guild.id
    
    # Mettre à jour tous les sticky messages
    if guild_id in sticky_messages:
        for channel_id in sticky_messages[guild_id]:
            sticky_messages[guild_id][channel_id]['bot_name'] = nom
    
    embed = discord.Embed(title="🤖 Nom Bot Modifié", description=f"**Nouveau nom:** {nom}\n**Par:** {interaction.user.mention}", color=0xa30174)
    await interaction.response.send_message(embed=embed)

# MODALS ET VIEWS
class GiveawayModal(discord.ui.Modal, title='Créer un Giveaway'):
    prize = discord.ui.TextInput(label='Prix du Giveaway', placeholder='Ex: Nitro Discord')
    duration = discord.ui.TextInput(label='Durée', placeholder='Ex: 30m, 2h, 1d (m=minutes, h=heures, d=jours)')
    winners = discord.ui.TextInput(label='Nombre de gagnants', placeholder='Ex: 1', default='1')
    description = discord.ui.TextInput(label='Description (optionnelle)', style=discord.TextStyle.paragraph, required=False)
    image_url = discord.ui.TextInput(label='Image URL (optionnelle)', required=False, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Parsing de la durée
            duration_str = self.duration.value.lower()
            duration_minutes = 0
            
            if duration_str.endswith('m'):
                duration_minutes = int(duration_str[:-1])
            elif duration_str.endswith('h'):
                duration_minutes = int(duration_str[:-1]) * 60
            elif duration_str.endswith('d'):
                duration_minutes = int(duration_str[:-1]) * 60 * 24
            else:
                # Si pas d'unité, considérer comme des minutes
                duration_minutes = int(duration_str)
            
            winner_count = int(self.winners.value)
            
            end_time = datetime.now() + timedelta(minutes=duration_minutes)
            
            embed = discord.Embed(
                title="🎉 GIVEAWAY 🎉",
                description=f"**Prix:** {self.prize.value}\n**Gagnants:** {winner_count}\n**Fin:** <t:{int(end_time.timestamp())}:R>",
                color=0xa30174
            )
            
            if self.description.value:
                embed.add_field(name="Description", value=self.description.value, inline=False)
            
            if self.image_url.value:
                embed.set_image(url=self.image_url.value)
            
            embed.set_footer(text="Réagissez avec 🎉 pour participer!")
            
            message = await interaction.response.send_message(embed=embed)
            msg = await interaction.original_response()
            await msg.add_reaction("🎉")
            
            giveaways[msg.id] = {
                'prize': self.prize.value,
                'end_time': end_time,
                'winner_count': winner_count,
                'participants': [],
                'active': True,
                'channel_id': interaction.channel.id
            }
            
        except ValueError:
            await interaction.response.send_message("❌ Durée ou nombre de gagnants invalide! Format de durée: 30m, 2h, 1d", ephemeral=True)

class EmbedModalComplete(discord.ui.Modal, title='Créer un Embed'):
    title_field = discord.ui.TextInput(
        label='Titre', 
        placeholder='Titre de l\'embed', 
        max_length=256
    )
    description = discord.ui.TextInput(
        label='Description', 
        style=discord.TextStyle.paragraph, 
        placeholder='Contenu principal (emojis persos: :nom_emoji:)',
        max_length=4000
    )
    color = discord.ui.TextInput(
        label='Couleur (hex)', 
        placeholder='#a30174', 
        required=False,
        default='#a30174',
        max_length=7
    )
    image_url = discord.ui.TextInput(
        label='Image URL (optionnel)', 
        required=False, 
        max_length=500,
        placeholder='https://exemple.com/image.png'
    )
    footer = discord.ui.TextInput(
        label='Footer (optionnel)', 
        required=False, 
        max_length=2048,
        placeholder='Texte en bas de l\'embed'
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Convertir la couleur
            try:
                color_value = int(self.color.value.replace('#', ''), 16) if self.color.value else 0xa30174
            except:
                color_value = 0xa30174
            
            # Traiter les emojis personnalisés dans la description
            description_text = self.description.value
            
            # Regex pour trouver les emojis personnalisés :nom_emoji:
            import re
            emoji_pattern = r':([a-zA-Z0-9_]+):'
            
            def replace_emoji(match):
                emoji_name = match.group(1)
                # Chercher l'emoji dans le serveur
                for emoji in interaction.guild.emojis:
                    if emoji.name == emoji_name:
                        return str(emoji)
                # Si pas trouvé, garder le texte original
                return match.group(0)
            
            description_text = re.sub(emoji_pattern, replace_emoji, description_text)
            
            # Traiter aussi le titre
            title_text = self.title_field.value
            title_text = re.sub(emoji_pattern, replace_emoji, title_text)
            
            # Créer l'embed
            embed = discord.Embed(
                title=title_text,
                description=description_text,
                color=color_value
            )
            
            if self.footer.value:
                embed.set_footer(text=self.footer.value)
            
            if self.image_url.value:
                embed.set_image(url=self.image_url.value)
            
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            print(f"[ERREUR EMBED] {e}")
            import traceback
            traceback.print_exc()
            await interaction.response.send_message(
                f"❌ Erreur lors de la création de l'embed: {str(e)}", 
                ephemeral=True
            )

class VouchModal(discord.ui.Modal, title='Laisser un Avis'):
    rating = discord.ui.TextInput(label='Note (/5)', placeholder='5')
    comment = discord.ui.TextInput(label='Commentaire', style=discord.TextStyle.paragraph, placeholder='Votre avis...')
    recommend = discord.ui.TextInput(label='Recommanderiez-vous? (oui/non)', placeholder='oui')
    image_url = discord.ui.TextInput(label='Image URL (optionnel)', required=False, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        data = get_guild_data(interaction.guild.id)
        config = data['config']['vouch_config']
        
        data['vouch_count'] += 1
        
        try:
            color_value = int(config['color'].replace('#', ''), 16)
        except:
            color_value = 0xa30174
        
        embed = discord.Embed(title=config['title'], color=color_value)
        embed.add_field(name="👤 Client", value=interaction.user.mention, inline=True)
        embed.add_field(name="⭐ Note", value=f"{self.rating.value}/5", inline=True)
        embed.add_field(name="💬 Commentaire", value=self.comment.value, inline=False)
        embed.add_field(name="👍 Recommande", value=self.recommend.value.title(), inline=True)
        embed.add_field(name="📊 Vouch #", value=data['vouch_count'], inline=True)
        embed.set_footer(text=config['footer'])
        
        if config['thumbnail']:
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
        
        if self.image_url.value:
            embed.set_image(url=self.image_url.value)
        
        await interaction.response.send_message(embed=embed)

class CustomKeyPanelModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title='Personnaliser Panel Key', timeout=300)
    
    title_field = discord.ui.TextInput(label='Titre', default='🔑 Clés Promoteur', max_length=256)
    description = discord.ui.TextInput(
        label='Description', 
        default='Récupérez vos clés promoteur', 
        style=discord.TextStyle.paragraph,
        max_length=4000
    )
    button_label = discord.ui.TextInput(label='Nom du bouton', default='Récupérer Clé', max_length=80)
    color = discord.ui.TextInput(label='Couleur (hex)', default='#0099ff', max_length=7)
    image_url = discord.ui.TextInput(label='Image URL (optionnel)', required=False, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        # SAUVEGARDER la configuration de l'embed
        data = get_guild_data(interaction.guild.id)
        data['config']['key_embed'] = {
            'title': self.title_field.value,
            'description': self.description.value,
            'color': self.color.value,
            'image_url': self.image_url.value if self.image_url.value else None,
            'button_label': self.button_label.value
        }
        
        # Créer l'embed avec les nouvelles données
        embed = create_key_embed(interaction.guild.id)
        
        # Créer la view avec le bouton personnalisé
        view = KeyPromotView(self.button_label.value)
        
        await interaction.response.send_message(embed=embed, view=view)
        
        # Confirmer la sauvegarde
        await interaction.followup.send("✅ Configuration de l'embed key promoteur sauvegardée! Elle sera utilisée par `/viewpanelkeypromot`", ephemeral=True)

class CustomFreeKeyPanelModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title='Personnaliser Panel Free Key', timeout=300)
    
    title_field = discord.ui.TextInput(label='Titre', default='🆓 Free Keys', max_length=256)
    description = discord.ui.TextInput(
        label='Description', 
        default='Récupérez votre clé gratuite', 
        style=discord.TextStyle.paragraph,
        max_length=4000
    )
    button_label = discord.ui.TextInput(label='Nom du bouton', default='Récupérer Free Key', max_length=80)
    color = discord.ui.TextInput(label='Couleur (hex)', default='#00ff00', max_length=7)
    image_url = discord.ui.TextInput(label='Image URL (optionnel)', required=False, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        # SAUVEGARDER la configuration de l'embed
        data = get_guild_data(interaction.guild.id)
        data['config']['freekey_embed'] = {
            'title': self.title_field.value,
            'description': self.description.value,
            'color': self.color.value,
            'image_url': self.image_url.value if self.image_url.value else None,
            'button_label': self.button_label.value
        }
        
        # Créer l'embed avec les nouvelles données
        embed = create_freekey_embed(interaction.guild.id)
        
        # Créer la view avec le bouton personnalisé
        view = FreeKeyView(self.button_label.value)
        
        await interaction.response.send_message(embed=embed, view=view)
        
        # Confirmer la sauvegarde
        await interaction.followup.send("✅ Configuration de l'embed free key sauvegardée! Elle sera utilisée par `/viewpanelfreekey`", ephemeral=True)

class InactivityMessageModal(discord.ui.Modal, title='Personnaliser Message Inactivité'):
    title_field = discord.ui.TextInput(
        label='Titre',
        placeholder='⏰ Ticket Inactif',
        default='⏰ Ticket Inactif',
        max_length=256
    )
    description = discord.ui.TextInput(
        label='Description',
        style=discord.TextStyle.paragraph,
        placeholder='Utilisez {hours} pour les heures et {mention} pour mentionner',
        default='Ce ticket est inactif depuis **{hours}h**.\n\n{mention}, souhaitez-vous :\n• Le garder ouvert 24h de plus ?\n• Le fermer définitivement ?',
        max_length=2000
    )
    color = discord.ui.TextInput(
        label='Couleur (hex)',
        placeholder='#ff9900',
        default='#ff9900',
        max_length=7
    )
    button_keep = discord.ui.TextInput(
        label='Texte bouton "Garder Ouvert"',
        placeholder='🔄 Garder Ouvert',
        default='🔄 Garder Ouvert',
        max_length=80
    )
    button_close = discord.ui.TextInput(
        label='Texte bouton "Fermer"',
        placeholder='🔒 Fermer le Ticket',
        default='🔒 Fermer le Ticket',
        max_length=80
    )

    async def on_submit(self, interaction: discord.Interaction):
        data = get_guild_data(interaction.guild.id)
        
        # Sauvegarder la config
        data['config']['inactivity_config']['embed'] = {
            'title': self.title_field.value,
            'description': self.description.value + '\n\n⚠️ **Fermeture automatique dans 24h** si pas de réponse.',
            'color': self.color.value,
            'image_url': None,
            'button_keep': self.button_keep.value,
            'button_close': self.button_close.value
        }
        
        # Aperçu
        try:
            color_value = int(self.color.value.replace('#', ''), 16)
        except:
            color_value = 0xff9900
        
        preview = discord.Embed(
            title=self.title_field.value,
            description=self.description.value.replace('{hours}', '24').replace('{mention}', interaction.user.mention) + '\n\n⚠️ **Fermeture automatique dans 24h** si pas de réponse.',
            color=color_value,
            timestamp=datetime.now()
        )
        preview.set_footer(text="Aperçu du message d'inactivité")
        
        await interaction.response.send_message("✅ **Message d'inactivité personnalisé !**\n\nAperçu :", embed=preview, ephemeral=True)

class CustomPanelModal(discord.ui.Modal, title='Personnaliser Panel Ticket'):
    title_field = discord.ui.TextInput(label='Titre', default='🎫 Système de Tickets', max_length=256)
    description_field = discord.ui.TextInput(
        label='Description', 
        default='Sélectionnez une catégorie pour ouvrir un ticket:', 
        style=discord.TextStyle.paragraph,
        max_length=4000
    )
    color = discord.ui.TextInput(label='Couleur (hex)', default='#a30174', max_length=7)
    image_url = discord.ui.TextInput(label='Image URL (optionnel)', required=False, max_length=500)
    thumbnail_url = discord.ui.TextInput(label='Thumbnail URL (optionnel)', required=False, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        # SAUVEGARDER la configuration de l'embed
        data = get_guild_data(interaction.guild.id)
        data['config']['ticket_embed'] = {
            'title': self.title_field.value,
            'description': self.description_field.value,
            'color': self.color.value,
            'image_url': self.image_url.value if self.image_url.value else None,
            'thumbnail_url': self.thumbnail_url.value if self.thumbnail_url.value else None
        }
        
        # Créer l'embed avec les nouvelles données
        embed = create_ticket_embed(interaction.guild.id)
        
        # Utiliser les catégories personnalisées
        view = TicketPanelView(interaction.guild.id)
        await interaction.response.send_message(embed=embed, view=view)
        
        # Confirmer la sauvegarde
        await interaction.followup.send("✅ Configuration de l'embed sauvegardée! Elle sera utilisée par `/viewpanelticket`", ephemeral=True)

# VIEWS POUR LES PANELS PERMANENTS
class TicketPanelView(discord.ui.View):
    def __init__(self, guild_id=None):
        super().__init__(timeout=None)  # Panel permanent
        if guild_id:
            # Utiliser les catégories personnalisées du serveur
            self.add_item(TicketSelect(guild_id))
        else:
            # Fallback avec catégories par défaut si pas de guild_id
            self.add_item(TicketSelectDefault())

class TicketSelect(discord.ui.Select):
    def __init__(self, guild_id):
        self.guild_id = guild_id
        
        # Créer les options avec les catégories les plus récentes
        options = create_ticket_options(guild_id)
        
        super().__init__(placeholder="Choisissez une catégorie...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await create_ticket(interaction, interaction.user, self.values[0])

class TicketSelectDefault(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Support", description="Support technique", value="support", emoji="🛠️"),
            discord.SelectOption(label="Bug Report", description="Signaler un bug", value="bug", emoji="🐛"),
            discord.SelectOption(label="Autre", description="Autres demandes", value="other", emoji="❓")
        ]
        super().__init__(placeholder="Choisissez une catégorie...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await create_ticket(interaction, interaction.user, self.values[0])

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Panel permanent

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.red, emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="🔒 Ticket Fermé", description=f"Ticket fermé par {interaction.user.mention}", color=0xff0000)
        await interaction.response.send_message(embed=embed)
        
        overwrites = interaction.channel.overwrites
        for target, overwrite in overwrites.items():
            if isinstance(target, discord.Member):
                overwrite.send_messages = False
                await interaction.channel.set_permissions(target, overwrite=overwrite)

    @discord.ui.button(label="Supprimer", style=discord.ButtonStyle.grey, emoji="🗑️")
    async def delete_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🗑️ Ticket sera supprimé dans 5 secondes...")
        await asyncio.sleep(5)
        
        # Transcript
        messages = []
        async for message in interaction.channel.history(limit=None, oldest_first=True):
            messages.append(f"[{message.created_at}] {message.author}: {message.content}")
        
        transcript = "\n".join(messages)
        
        try:
            user_id = int(interaction.channel.name.split('-')[1])
            user = bot.get_user(user_id)
            if user:
                file = discord.File(io.StringIO(transcript), filename=f"transcript-{interaction.channel.name}.txt")
                await user.send(f"📄 Transcript du ticket {interaction.channel.name}:", file=file)
        except:
            pass
        
        await interaction.channel.delete()

class KeyPromotView(discord.ui.View):
    def __init__(self, button_label="Récupérer Clé"):
        super().__init__(timeout=None)  # Panel permanent
        self.button_label = button_label
        # Créer le bouton avec le label personnalisé
        button = discord.ui.Button(label=button_label, style=discord.ButtonStyle.primary, emoji="🔑")
        button.callback = self.get_key
        self.add_item(button)

    async def get_key(self, interaction: discord.Interaction):
        data = get_guild_data(interaction.guild.id)
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        
        # Vérifier rôles autorisés
        if data['config']['key_roles']:
            user_roles = [role.id for role in interaction.user.roles]
            if not any(role_id in user_roles for role_id in data['config']['key_roles']):
                await interaction.response.send_message("❌ Vous n'avez pas les rôles requis!", ephemeral=True)
                return
        
        # Vérifier cooldown
        cooldown_key = f"{guild_id}_{user_id}_key"
        if cooldown_key in user_cooldowns:
            remaining = user_cooldowns[cooldown_key] - datetime.now()
            if remaining.total_seconds() > 0:
                minutes = int(remaining.total_seconds() / 60)
                await interaction.response.send_message(f"⏰ Cooldown actif! Attendez encore {minutes} minutes.", ephemeral=True)
                return
        
        # Donner clé
        if not data['keys']:
            await interaction.response.send_message("❌ Plus de clés disponibles!", ephemeral=True)
            return
        
        key = data['keys'].pop(0)
        user_cooldowns[cooldown_key] = datetime.now() + timedelta(minutes=data['config']['key_cooldown'])
        
        try:
            await interaction.user.send(f"🔑 **Votre clé promoteur:** `{key}`")
            await interaction.response.send_message("✅ Clé envoyée en privé!", ephemeral=True)
        except:
            await interaction.response.send_message(f"🔑 **Votre clé:** `{key}`\n⚠️ Supprimez ce message après utilisation!", ephemeral=True)

class FreeKeyView(discord.ui.View):
    def __init__(self, button_label="Récupérer Free Key"):
        super().__init__(timeout=None)  # Panel permanent
        self.button_label = button_label
        # Créer le bouton avec le label personnalisé
        button = discord.ui.Button(label=button_label, style=discord.ButtonStyle.success, emoji="🆓")
        button.callback = self.get_free_key
        self.add_item(button)

    async def get_free_key(self, interaction: discord.Interaction):
        data = get_guild_data(interaction.guild.id)
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        
        # Vérifier si déjà pris
        if guild_id not in free_key_users:
            free_key_users[guild_id] = set()
        
        if user_id in free_key_users[guild_id]:
            await interaction.response.send_message("❌ Vous avez déjà récupéré votre free key!", ephemeral=True)
            return
        
        # Donner free key
        if not data['free_keys']:
            await interaction.response.send_message("❌ Plus de free keys disponibles!", ephemeral=True)
            return
        
        key = data['free_keys'].pop(0)
        free_key_users[guild_id].add(user_id)
        
        try:
            await interaction.user.send(f"🆓 **Votre free key:** `{key}`")
            await interaction.response.send_message("✅ Free key envoyée en privé!", ephemeral=True)
        except:
            await interaction.response.send_message(f"🆓 **Votre free key:** `{key}`\n⚠️ Supprimez ce message après utilisation!", ephemeral=True)

class InactivityView(discord.ui.View):
    def __init__(self, guild_id, channel_id, creator_id):
        super().__init__(timeout=None)  # Permanent
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.creator_id = creator_id
        
        # Récupérer les labels des boutons depuis la config
        data = get_guild_data(guild_id)
        config = data['config']['inactivity_config']
        
        # Créer les boutons avec custom_id UNIQUE
        keep_button = discord.ui.Button(
            style=discord.ButtonStyle.success,
            label=config['embed']['button_keep'],
            custom_id=f"inactivity_keep_{channel_id}_{int(datetime.now().timestamp())}"
        )
        keep_button.callback = self.keep_open
        
        close_button = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label=config['embed']['button_close'],
            custom_id=f"inactivity_close_{channel_id}_{int(datetime.now().timestamp())}"
        )
        close_button.callback = self.close_ticket
        
        self.add_item(keep_button)
        self.add_item(close_button)
    
    async def keep_open(self, interaction: discord.Interaction):
        """Garder le ticket ouvert"""
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message(
                "❌ Seul le créateur du ticket peut utiliser ce bouton.",
                ephemeral=True
            )
            return
        
        print(f"[INACTIVITY] Ticket {self.channel_id} gardé ouvert par le créateur")
        
        # Reset l'activité
        update_ticket_activity(self.guild_id, self.channel_id, self.creator_id)
        
        # Incrémenter le compteur d'extensions
        if self.guild_id in ticket_activity_tracker:
            if self.channel_id in ticket_activity_tracker[self.guild_id]:
                ticket_activity_tracker[self.guild_id][self.channel_id]['extensions'] = ticket_activity_tracker[self.guild_id][self.channel_id].get('extensions', 0) + 1
        
        # Supprimer le message d'avertissement
        try:
            await interaction.message.delete()
        except:
            pass
        
        # Confirmation
        await interaction.response.send_message(
            "✅ **Ticket gardé ouvert**\n"
            "Le ticket restera ouvert pour 24h supplémentaires.\n"
            "Un nouveau rappel sera envoyé en cas d'inactivité.",
            ephemeral=True
        )
    
    async def close_ticket(self, interaction: discord.Interaction):
        """Fermer le ticket - VERSION SIMPLIFIÉE ET ROBUSTE"""
        
        # Vérification créateur
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message(
                "❌ Seul le créateur du ticket peut fermer ce ticket.",
                ephemeral=True
            )
            return
        
        # Déférer la réponse pour avoir plus de temps
        await interaction.response.defer()
        
        try:
            # Message de confirmation
            await interaction.followup.send(
                "🗑️ **Fermeture du ticket en cours...**\n"
                "📄 Le transcript sera envoyé en DM et dans le salon de logs.",
                ephemeral=False
            )
            
            await asyncio.sleep(2)
            
            # Variables
            channel = interaction.channel
            guild = interaction.guild
            
            # 1. Retirer du tracker
            remove_ticket_from_tracker(self.guild_id, self.channel_id)
            
            # 2. Créer le transcript
            transcript_text = await create_ticket_transcript(channel)
            
            # 3. Extraire infos
            channel_parts = channel.name.split('-')
            ticket_number = channel_parts[-1] if len(channel_parts) >= 4 else "N/A"
            ticket_category = channel_parts[1].title() if len(channel_parts) >= 4 else "N/A"
            
            # 4. Trouver le créateur
            creator_user = guild.get_member(self.creator_id)
            
            ticket_info = {
                'number': ticket_number,
                'category': ticket_category,
                'creator': creator_user.mention if creator_user else f"<@{self.creator_id}>"
            }
            
            # 5. Envoyer logs
            await send_ticket_log(
                guild,
                channel.name,
                ticket_info,
                transcript_text,
                interaction.user
            )
            
            # 6. Envoyer DM
            if creator_user:
                try:
                    dm_embed = discord.Embed(
                        title="📄 Transcript de votre ticket",
                        description="**Raison:** Fermé suite à inactivité",
                        color=0xa30174,
                        timestamp=datetime.now()
                    )
                    dm_embed.add_field(name="🎫 Ticket", value=channel.name, inline=True)
                    dm_embed.add_field(name="📊 Numéro", value=f"#{ticket_number}", inline=True)
                    dm_embed.add_field(name="🏷️ Catégorie", value=ticket_category, inline=True)
                    
                    filename = f"transcript-{channel.name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
                    file_dm = discord.File(io.StringIO(transcript_text), filename=filename)
                    await creator_user.send(embed=dm_embed, file=file_dm)
                except:
                    pass
            
            # 7. Supprimer le salon
            await channel.delete(reason=f"Ticket fermé par inactivité - {interaction.user}")
        
        except Exception as e:
            print(f"[INACTIVITY] ERREUR: {e}")
            import traceback
            traceback.print_exc()
            
            try:
                await interaction.followup.send(
                    f"❌ Erreur lors de la fermeture: {str(e)}\n"
                    f"Utilisez `/deleteticket` manuellement.",
                    ephemeral=True
                )
            except:
                pass

# HELP VIEW PERMANENT
class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Panel permanent
        self.add_item(HelpSelect())

class HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="🎉 Giveaways", 
                description="5 commandes - Créer et gérer des giveaways", 
                value="giveaways",
                emoji="🎉"
            ),
            discord.SelectOption(
                label="🔨 Modération", 
                description="15 commandes - Ban, kick, mute, warn, clear...", 
                value="moderation",
                emoji="🔨"
            ),
            discord.SelectOption(
                label="🛡️ Auto-Modération", 
                description="12 commandes - Anti-lien, anti-spam, mots interdits", 
                value="automod",
                emoji="🛡️"
            ),
            discord.SelectOption(
                label="🎭 Gestion Rôles", 
                description="4 commandes - Autorôle, ajouter/retirer rôles", 
                value="roles",
                emoji="🎭"
            ),
            discord.SelectOption(
                label="ℹ️ Informations", 
                description="4 commandes - Userinfo, serverinfo, warnings", 
                value="info",
                emoji="ℹ️"
            ),
            discord.SelectOption(
                label="⚙️ Configuration", 
                description="6 commandes - Config générale, logs, permissions", 
                value="config",
                emoji="⚙️"
            ),
            discord.SelectOption(
                label="🎯 Système Vouchs", 
                description="5 commandes - Avis clients personnalisables", 
                value="vouchs",
                emoji="🎯"
            ),
            discord.SelectOption(
                label="🔊 Salons Vocaux", 
                description="2 commandes - Vocaux temporaires, bienvenue", 
                value="voice",
                emoji="🔊"
            ),
            discord.SelectOption(
                label="📊 Sondages", 
                description="1 commande - Créer des sondages interactifs", 
                value="polls",
                emoji="📊"
            ),
            discord.SelectOption(
                label="🎫 Système Tickets", 
                description="16 commandes - Tickets avec logs automatiques", 
                value="tickets",
                emoji="🎫"
            ),
            discord.SelectOption(
                label="🔑 Key Promoteur", 
                description="9 commandes - Système de clés avec cooldown", 
                value="keys",
                emoji="🔑"
            ),
            discord.SelectOption(
                label="🔓 Free Key", 
                description="8 commandes - Clés gratuites personnalisables", 
                value="freekeys",
                emoji="🔓"
            ),
            discord.SelectOption(
                label="📌 Sticky Messages", 
                description="6 commandes - Messages qui restent en bas", 
                value="sticky",
                emoji="📌"
            )
        ]
        super().__init__(placeholder="Choisissez une catégorie...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        
        embeds = {
            "giveaways": discord.Embed(
                title="🎉 Commandes Giveaways",
                description="**5 commandes disponibles**",
                color=0xff69b4
            ).add_field(name="Commandes", value="""
`/gcreate` - Créer un giveaway avec panneau interactif (+ image)
`/greroll <message_id>` - Relancer un giveaway
`/glist` - Lister les giveaways actifs
`/gend <message_id>` - Terminer un giveaway prématurément
`/gdelete <message_id>` - Supprimer un giveaway
            """, inline=False),
            
            "moderation": discord.Embed(
                title="🔨 Commandes Modération",
                description="**15 commandes disponibles**",
                color=0xff0000
            ).add_field(name="Commandes", value="""
`/ban <member> [reason]` - Bannir un membre
`/kick <member> [reason]` - Exclure un membre
`/mute <member> <duration> [reason]` - Rendre muet un membre
`/unmute <member>` - Enlever le mute
`/unban <user_id>` - Débannir un utilisateur
`/clear <amount>` - Supprimer des messages
`/warn <member> <reason>` - Donner un avertissement
`/clearwarnings <member>` - Effacer les avertissements
`/nuke` - Supprimer tous les messages (recrée le salon)
`/locksalon [channel]` - Verrouiller un salon
`/unlocksalon [channel]` - Déverrouiller un salon
`/embed` - Créer un embed avancé (+ images, URL cliquable)
`/slowmode <seconds> [channel]` - Configurer le mode lent
`/removeslowmode [channel]` - Supprimer le mode lent
`/massban <reason>` - Bannissement en masse (fichier .txt requis)
            """, inline=False),
            
            "automod": discord.Embed(
                title="🛡️ Commandes Auto-Modération",
                description="**12 commandes disponibles**",
                color=0x00bfff
            ).add_field(name="Commandes", value="""
`/automod <status>` - Configurer l'auto-modération générale
`/antilink_config <status> <action>` - Configurer l'anti-lien
`/antispam_config <status> <action>` - Configurer l'anti-spam
`/antiraid_config <status> <action>` - Configurer l'anti-raid
`/antilink <status>` - Activer/désactiver l'anti-lien
`/antilinkaction <action>` - Configurer l'action anti-lien
`/whitelist_add <domain>` - Ajouter un domaine autorisé
`/whitelist_remove <domain>` - Retirer un domaine autorisé
`/whitelist_list` - Voir la liste blanche des domaines
`/badwordaction <action>` - Configurer l'action pour les mots interdits
`/addword <word>` - Ajouter un mot interdit
`/removeword <word>` - Retirer un mot interdit
            """, inline=False),
            
            "roles": discord.Embed(
                title="🎭 Commandes Gestion Rôles",
                description="**4 commandes disponibles**",
                color=0x9932cc
            ).add_field(name="Commandes", value="""
`/autorole <role>` - Configurer le rôle automatique pour les nouveaux membres
`/autorole_remove` - Supprimer l'autorôle
`/addrole <member> <role>` - Ajouter un rôle à un membre
`/removerole <member> <role>` - Retirer un rôle à un membre
            """, inline=False),
            
            "info": discord.Embed(
                title="ℹ️ Commandes Informations",
                description="**4 commandes disponibles**",
                color=0x00ff7f
            ).add_field(name="Commandes", value="""
`/userinfo [member]` - Afficher les informations d'un utilisateur
`/serverinfo` - Afficher les informations du serveur
`/warnings [member]` - Voir les avertissements d'un membre
`/listwords` - Voir la liste des mots interdits (en privé)
            """, inline=False),
            
            "config": discord.Embed(
                title="⚙️ Commandes Configuration",
                description="**6 commandes disponibles**",
                color=0x1e90ff
            ).add_field(name="Commandes", value="""
`/config` - Voir la configuration complète du bot
`/setlogs <channel>` - Configurer le salon de logs
`/setlogs_remove` - Supprimer le salon de logs
`/setrole <role>` - Configurer les rôles autorisés à utiliser le bot
`/unsetroles <role>` - Retirer un rôle des autorisations
`/help` - Afficher ce menu d'aide
            """, inline=False),
            
            "vouchs": discord.Embed(
                title="🎯 Commandes Système Vouchs",
                description="**5 commandes disponibles**",
                color=0xff6347
            ).add_field(name="Commandes", value="""
`/vouch` - Laisser un avis client avec formulaire interactif (+ image)
`/modifembed <titre> <couleur> <footer> <thumbnail>` - Personnaliser l'apparence des embeds
`/resetcount` - Remettre le compteur de vouchs à zéro
`/configembed` - Voir la configuration actuelle des vouchs
`/stats` - Afficher les statistiques complètes du serveur
            """, inline=False),
            
            "voice": discord.Embed(
                title="🔊 Commandes Salons Vocaux",
                description="**2 commandes disponibles**",
                color=0x32cd32
            ).add_field(name="Commandes", value="""
`/tempvoice <nom> [max_users]` - Créer un salon vocal temporaire
`/welcome-set <channel> <message>` - Configurer le message de bienvenue
            """, inline=False),
            
            "polls": discord.Embed(
                title="📊 Commandes Sondages",
                description="**1 commande disponible**",
                color=0xffd700
            ).add_field(name="Commandes", value="""
`/poll <question> <option1> <option2> [option3] [option4] [duration]` - Créer un sondage avec réactions automatiques
            """, inline=False),
            
            "tickets": discord.Embed(
                title="🎫 Commandes Système Tickets",
                description="**16 commandes disponibles**",
                color=0x8a2be2
            ).add_field(name="📋 Panels & Affichage", value="""
`/viewpanelticket` - Afficher le panneau avec menu déroulant (permanent)
`/custompanel` - Personnaliser l'embed du panneau (+ sauvegarde auto)
            """, inline=False).add_field(name="⚙️ Configuration & Gestion", value="""
`/category <action> <nom> [nouveau_nom] [description] [emoji]` - Modifier les catégories
`/setroleticket <role> <action>` - Ajouter/retirer des rôles autorisés
`/setcategory <category>` - Définir la catégorie Discord des tickets
`/configticket` - Voir la configuration actuelle complète
`/synctickets` - Forcer la synchronisation des catégories
`/resetticket` - Remettre la configuration par défaut
`/presetticket <preset>` - Charger un preset d'embed (default/modern/elegant/gaming)
`/ticketstats` - Voir les statistiques des tickets
            """, inline=False).add_field(name="📋 Logs & Transcripts", value="""
`/setticketlogs <channel>` - Définir le salon de logs tickets (transcripts .txt)
`/removeticketlogs` - Supprimer le salon de logs tickets
            """, inline=False).add_field(name="🎫 Actions dans les Tickets", value="""
`/closeticket` - Fermer un ticket (dans le salon ticket)
`/deleteticket` - Supprimer un ticket avec transcript (dans le salon ticket)
`/openticket <member> <category>` - Ouvrir un ticket manuellement
`/ticket-create <category>` - Créer un ticket dans une catégorie spécifique
            """, inline=False).add_field(
                name="✨ Système de Logs",
                value="• **Transcripts automatiques** en .txt\n• **Envoi dans salon de logs** configuré\n• **Envoi en DM** au créateur du ticket\n• **Format complet** : messages, embeds, fichiers",
                inline=False
            ),
            
            "keys": discord.Embed(
                title="🔑 Commandes Key Promoteur",
                description="**9 commandes disponibles**",
                color=0xff4500
            ).add_field(name="📋 Panels & Affichage", value="""
`/viewpanelkeypromot` - Afficher le panel pour récupérer des clés (permanent)
`/custompanelkey` - Personnaliser l'embed du panel key (+ images, bouton custom)
            """, inline=False).add_field(name="⚙️ Gestion & Configuration", value="""
`/addkey <keys>` - Ajouter clés (séparées par espaces : KEY1 KEY2 KEY3)
`/removekey <key>` - Supprimer une clé du stock
`/stockkey` - Voir le nombre de clés disponibles
`/setrolekey <role>` - Définir les rôles autorisés à récupérer des clés
`/setcooldownkey <minutes>` - Définir le cooldown entre les récupérations
`/configkey` - Voir la configuration actuelle du panel key
`/resetkeyconfig` - Remettre la configuration par défaut
            """, inline=False).add_field(
                name="✨ Synchronisation",
                value="• `/custompanelkey` **sauvegarde automatiquement**\n• `/viewpanelkeypromot` **utilise la config sauvegardée**\n• **Parfaite synchronisation** entre les deux commandes",
                inline=False
            ),
            
            "freekeys": discord.Embed(
                title="🔓 Commandes Free Key",
                description="**8 commandes disponibles**",
                color=0x00ff00
            ).add_field(name="📋 Panels & Affichage", value="""
`/viewpanelfreekey` - Afficher le panel pour récupérer des clés gratuites (permanent)
`/custompanelfreekey` - Personnaliser l'embed du panel (+ images, bouton custom)
            """, inline=False).add_field(name="⚙️ Gestion & Configuration", value="""
`/addfreekey <keys>` - Ajouter free keys (séparées par espaces : FREE1 FREE2 FREE3)
`/removefreekey <key>` - Supprimer une free key du stock
`/stockfreekey` - Voir le stock de free keys
`/resetfreekey` - Reset la liste des utilisateurs (permet de récupérer à nouveau)
`/configfreekey` - Voir la configuration actuelle du panel free key
`/resetfreekeyconfig` - Remettre la configuration par défaut
            """, inline=False).add_field(
                name="✨ Synchronisation",
                value="• `/custompanelfreekey` **sauvegarde automatiquement**\n• `/viewpanelfreekey` **utilise la config sauvegardée**\n• **Parfaite synchronisation** entre les deux commandes",
                inline=False
            ),
            
            "sticky": discord.Embed(
                title="📌 Commandes Sticky Messages",
                description="**6 commandes disponibles**",
                color=0xffa500
            ).add_field(name="Commandes", value="""
`/stick <message>` - Créer un message qui reste toujours en bas du salon
`/stickstop` - Arrêter temporairement le sticky dans le salon actuel
`/stickstart` - Redémarrer le sticky précédemment arrêté
`/stickdelete` - Supprimer complètement le sticky du salon
`/getsticks` - Voir tous les messages sticky du serveur
`/setnamestick <nom>` - Modifier le nom du bot affiché dans les messages sticky
            """, inline=False)
        }
        
        embed = embeds.get(category)
        if embed:
            embed.set_footer(text="💡 Panels permanents : Tickets, Keys, Free Keys, Help | Bot créé par TEKAZ")
            await interaction.response.edit_message(embed=embed, view=self.view)

# EVENT POUR LES RÉACTIONS DE GIVEAWAY
@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    
    if payload.message_id in giveaways and str(payload.emoji) == "🎉":
        if payload.user_id not in giveaways[payload.message_id]['participants']:
            giveaways[payload.message_id]['participants'].append(payload.user_id)

@bot.event
async def on_raw_reaction_remove(payload):
    if payload.user_id == bot.user.id:
        return
    
    if payload.message_id in giveaways and str(payload.emoji) == "🎉":
        if payload.user_id in giveaways[payload.message_id]['participants']:
            giveaways[payload.message_id]['participants'].remove(payload.user_id)



class TranslateView(discord.ui.View):
    def __init__(self, text: str):
        super().__init__(timeout=None)
        self.text = text

    @discord.ui.button(
        label="Translate",
        emoji="🇬🇧",
        style=discord.ButtonStyle.secondary
    )
    async def translate(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            translated = GoogleTranslator(
                source="auto",
                target="en"
            ).translate(self.text)

            embed = discord.Embed(
                title="🌍 Translation (EN)",
                description=translated,
                color=0x3498db
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

        except Exception:
            await interaction.response.send_message(
                "❌ Translation impossible.",
                ephemeral=True
            )



@bot.event
async def on_message(message: discord.Message):
    await bot.process_commands(message)

    # On ne traite QUE les messages du bot
    if not bot.user or message.author.id != bot.user.id:
        return

    # Pas d'embed
    if not message.embeds:
        return

    # Déjà un bouton
    if message.components:
        return

    # Construire le texte à traduire
    texts = []
    for embed in message.embeds:
        if embed.title:
            texts.append(embed.title)
        if embed.description:
            texts.append(embed.description)
        for field in embed.fields:
            texts.append(field.name)
            texts.append(field.value)

    full_text = "\n".join(texts).strip()
    if not full_text:
        return

    # ⏳ Petite pause pour laisser Discord "stabiliser" le message
    await asyncio.sleep(0.3)

    try:
        await message.edit(view=TranslateView(full_text))

    except discord.NotFound:
        # Message supprimé ou non éditable → on ignore silencieusement
        return

    except discord.Forbidden:
        # Permissions insuffisantes
        return

    except discord.HTTPException:
        # Autre erreur HTTP (rate limit, etc.)
        return


@bot.command(name="dm")
async def dm(ctx, user: discord.User, *, message: str):
    # Vérification permissions (admin / rôles autorisés)
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Tu n'as pas la permission d'utiliser cette commande.")
        return

    try:
        embed = discord.Embed(
            title="📩 Message privé",
            description=message,
            color=0x5865F2,
            timestamp=datetime.now()
        )

        embed.set_footer(
            text=f"Envoyé depuis {ctx.guild.name}"
        )

        await user.send(embed=embed)

        await ctx.send(
            f"✅ Message envoyé à **{user}**"
        )

    except discord.Forbidden:
        await ctx.send(
            "❌ Impossible d'envoyer le message (DM fermés)."
        )

    except Exception:
        await ctx.send(
            "❌ Erreur lors de l'envoi du message."
        )



@bot.tree.command(
    name="redeembot",
    description="Utiliser une clé pour recevoir l'accès"
)
@app_commands.describe(key="Clé reçue après l'achat")
async def redeembot(interaction: discord.Interaction, key: str):
    data = get_guild_data(interaction.guild.id)

    # Sécurité structure
    if "used_keys" not in data:
        data["used_keys"] = {}

    # Clé invalide
    if key not in data["keys"]:
        await interaction.response.send_message(
            "❌ Clé invalide ou déjà utilisée.",
            ephemeral=True
        )
        return

    # Consommer la clé
    data["keys"].remove(key)
    data["used_keys"][key] = interaction.user.id

    # Envoi du DM
    try:
        embed = discord.Embed(
            title="✅ Clé activée avec succès",
            description=(
                "Merci pour ton achat 💜\n\n"
                "Voici ton accès exclusif 👇\n"
                "**🔗 https://gofile.io/d/ff6hfn**"
            ),
            color=0x57F287
        )

        await interaction.user.send(embed=embed)

        await interaction.response.send_message(
            "✅ Clé validée ! Le lien t’a été envoyé en message privé.",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Impossible de t’envoyer un DM.\nActive tes messages privés puis réessaie.",
            ephemeral=True
        )


@bot.tree.command(
    name="usedkeys",
    description="Voir les clés déjà utilisées"
)
async def usedkeys(interaction: discord.Interaction):
    if not await check_permissions(interaction):
        return

    data = get_guild_data(interaction.guild.id)
    used = data.get("used_keys", {})

    if not used:
        await interaction.response.send_message(
            "📭 Aucune clé utilisée.",
            ephemeral=True
        )
        return

    desc = "\n".join(
        f"`{k}` → <@{v}>"
        for k, v in used.items()
    )

    embed = discord.Embed(
        title="🔐 Clés utilisées",
        description=desc,
        color=0xed4245
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print("✅ Commandes slash synchronisées")

# DÉMARRAGE DU BOT
if __name__ == "__main__":
    bot.run(os.getenv('DISCORD_TOKEN'))
            
