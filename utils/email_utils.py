import aiosmtplib
from email.message import EmailMessage
import datetime
import logging
import os
from jinja2 import Environment, FileSystemLoader
from database import db

logger = logging.getLogger(__name__)

# Base URL for links (should be configured in .env, fallback to localhost)
BASE_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# Initialize Jinja2 environment
template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')
template_env = Environment(loader=FileSystemLoader(template_dir))

EMAIL_TRANSLATIONS = {
    "fr": {
        "disclaimer": "Cet email a été généré automatiquement par LogForge.",
        "performed_by": "Action effectuée par",
        "view_dashboard": "Ouvrir le Tableau de Bord",
        "view_logs": "Explorer les Logs",
        "alert": {
            "title": "Alerte Système",
            "triggered_by": "Alerte déclenchée par",
            "project": "Projet",
            "env": "Environnement",
            "channel": "Canal",
            "container": "Conteneur",
            "date": "Date",
            "message": "Message",
            "metadata": "Contexte Additionnel",
            "stack": "Trace d'exécution"
        },
        "project_access": {
            "granted": {
                "subject": "Accès accordé au projet : {name}",
                "title": "Nouvel Accès Projet",
                "body_text": "L'équipe d'administration de LogForge vient de vous intégrer à un nouveau projet. Vous avez désormais un accès en lecture sur l'environnement suivant :",
                "footer_text": "Connectez-vous dès à présent à votre tableau de bord pour explorer les journaux de ce projet."
            },
            "revoked": {
                "subject": "Accès révoqué pour le projet : {name}",
                "title": "Accès Projet Révoqué",
                "body_text": "Dans le cadre de la gestion continue des accès et de la sécurité, nous vous informons que vos droits sur le projet suivant ont été révoqués :",
                "footer_text": "Vous ne recevrez plus d'alertes concernant ce projet et n'y aurez plus accès via votre interface."
            },
            "item_label": "Projet concerné",
            "hi": "Bonjour {name},"
        },
        "permission_change": {
            "granted": {
                "subject": "Privilège d'administration accordé : {name}",
                "title": "Permission Étendue",
                "body_text": "L'équipe d'administration de LogForge a le plaisir de vous informer que vos droits sur la plateforme ont été étendus. La permission suivante vous a été formellement attribuée :",
                "footer_text": "Cette mise à jour prend effet immédiatement. Elle vous permet d'accéder à de nouvelles fonctionnalités de gestion."
            },
            "revoked": {
                "subject": "Privilège d'administration révoqué : {name}",
                "title": "Permission Retirée",
                "body_text": "Suite à une révision des habilitations, nous vous informons qu'un privilège a été retiré de votre profil utilisateur. La permission concernée est :",
                "footer_text": "Si vous estimez que ce retrait impacte directement votre travail, merci de vous rapprocher de l'équipe informatique."
            },
            "item_label": "Nouveau paramétrage",
            "hi": "Bonjour {name},",
            "labels": {
                "manage_smtp": "Configuration avancée du serveur SMTP",
                "manage_alert_rules": "Gestion intégrale des Systèmes d'Alerte",
                "delete_projects": "Suppression définitive des Projets",
                "view_docker_logs": "Consultation des Logs Docker",
                "unknown": "Privilège Spécifique"
            }
        },
        "smtp_test": {
            "subject": "Test de Configuration SMTP",
            "title": "Connexion Réussie !",
            "body_text": "Votre configuration SMTP fonctionne correctement.",
            "info_text": "Ceci est un email de test généré par LogForge."
        },
        "role_change": {
            "subject": "Une mise à jour importante de votre rôle a eu lieu : {role}",
            "title": "Évolution de votre Rôle LogForge",
            "body_text": "Nous vous informons qu'une mise à jour administrative a été appliquée à votre profil utilisateur. Votre niveau d'accès et vos responsabilités sur la plateforme ont évolué. Votre nouveau rôle est :",
            "footer_text": "Veuillez noter que ce changement redéfinit de manière globale ce que vous pouvez voir et modifier au sein de l'application.",
            "hi": "Bonjour {name},",
            "item_label": "Votre nouveau rôle"
        },
        "status_change": {
            "activated": {
                "subject": "Activation de votre compte LogForge",
                "title": "Compte Opérationnel",
                "body_text": "C'est officiel ! Votre profil utilisateur LogForge a été entièrement activé par l'équipe d'administration. Votre environnement de travail est désormais pleinement opérationnel.",
                "footer_text": "L'intégralité des fonctionnalités de la plateforme s'offre à vous. Connectez-vous dès aujourd'hui."
            },
            "deactivated": {
                "subject": "Suspension temporaire de votre compte LogForge",
                "title": "Accès Suspendu",
                "body_text": "Important : Nous vous signalons que votre accès au compte LogForge a été temporairement gelé par mesure administrative ou de sécurité.",
                "footer_text": "Afin d'obtenir plus d'informations ou de rétablir vos accès, nous vous recommandons de contacter d'urgence votre responsable."
            },
            "hi": "Bonjour {name},"
        },
        "password_reset": {
            "subject": "Réinitialisation de votre mot de passe LogForge",
            "title": "Changement de Mot de Passe",
            "body_text": "Vous avez demandé la réinitialisation de votre mot de passe pour accéder à LogForge. Cliquez sur le bouton ci-dessous pour définir un nouveau mot de passe sécurisé :",
            "cta_label": "Réinitialiser mon mot de passe",
            "footer_text": "Ce lien expirera dans 1 heure. Si vous n'êtes pas à l'origine de cette demande, vous pouvez ignorer cet email en toute sécurité.",
            "hi": "Bonjour {name},"
        },
        "agent_key_expiration": {
            "subject": "Expiration imminente d'une clé d'agent Docker",
            "title": "Alerte Expiration de Clé",
            "body_text": "Nous vous informons que la clé d'agent Docker suivante va bientôt expirer. Une fois expirée, les logs envoyés avec cette clé seront rejetés.",
            "hi": "Bonjour l'équipe,",
            "item_label": "Clé concernée",
            "expiration_label": "Date d'expiration",
            "remaining_label": "Temps restant",
            "footer_text": "Veuillez générer une nouvelle clé dans les paramètres pour assurer la continuité de la collecte des logs."
        },
        "invitation": {
            "subject": "Vous avez été invité à rejoindre {company} sur LogForge",
            "badge": "Invitation",
            "title": "Vous avez été invité !",
            "body_intro": "{inviter} vous invite à rejoindre son espace sur LogForge.",
            "body_sub": "Créez votre compte gratuitement en cliquant sur le bouton ci-dessous.",
            "joining_label": "Vous allez rejoindre",
            "cta_label": "Accepter l'invitation",
            "expiry_note": "Ce lien expire dans 24 heures. Si vous n'attendiez pas cette invitation, ignorez cet email.",
            "link_fallback": "Si le bouton ne fonctionne pas, copiez ce lien dans votre navigateur :"
        }
    },
    "en": {
        "disclaimer": "This email was automatically generated by LogForge.",
        "performed_by": "Action performed by",
        "view_dashboard": "Open Dashboard",
        "view_logs": "Explore Logs",
        "alert": {
            "title": "System Alert",
            "triggered_by": "Alert triggered by",
            "project": "Project",
            "env": "Environment",
            "channel": "Channel",
            "container": "Container",
            "date": "Timestamp",
            "message": "Message",
            "metadata": "Additional Context",
            "stack": "Stack Trace"
        },
        "project_access": {
            "granted": {
                "subject": "Access granted to log project: {name}",
                "title": "New Platform Access",
                "body_text": "The LogForge administration team has just onboarded you to a new project. You now have full read-access to the following environment:",
                "footer_text": "Log in to your dashboard now to start exploring the event logs for this project."
            },
            "revoked": {
                "subject": "Access revoked for log project: {name}",
                "title": "Project Access Revoked",
                "body_text": "As part of ongoing access management and security reviews, we would like to inform you that your permissions for the following project have been revoked:",
                "footer_text": "You will no longer receive alerts for this project, nor will you be able to view its logs via your interface."
            },
            "item_label": "Target Project",
            "hi": "Hi {name},"
        },
        "permission_change": {
            "granted": {
                "subject": "Administrative privilege granted: {name}",
                "title": "Privilege Extended",
                "body_text": "The LogForge administration team is pleased to inform you that your platform rights have been extended. The following administrative permission has been assigned to your profile:",
                "footer_text": "This update takes effect immediately. It grants you access to new management functionalities."
            },
            "revoked": {
                "subject": "Administrative privilege revoked: {name}",
                "title": "Privilege Retracted",
                "body_text": "Following a routine access review, we are informing you that a specific privilege has been withdrawn from your user profile. The permission affected is:",
                "footer_text": "If you believe this revocation directly impacts your day-to-day operations, please contact the IT team."
            },
            "item_label": "Affected Permission",
            "hi": "Hi {name},",
            "labels": {
                "manage_smtp": "Advanced SMTP Server Configuration",
                "manage_alert_rules": "Full Management of Alert Systems",
                "delete_projects": "Permanent Deletion of Projects",
                "view_docker_logs": "Monitor Docker Agent Logs",
                "unknown": "Specific Privilege"
            }
        },
        "smtp_test": {
            "subject": "SMTP Configuration Test",
            "title": "Connection Successful!",
            "body_text": "Your SMTP configuration is working correctly.",
            "info_text": "This is a test email generated from LogForge."
        },
        "role_change": {
            "subject": "Important update to your LogForge role: {role}",
            "title": "LogForge Role Evolution",
            "body_text": "We would like to inform you that an administrative update has been applied to your user profile. Your access level and responsibilities on the platform have evolved. Your new designated role is:",
            "footer_text": "Please note that this change comprehensively redefines what you can view and modify within the application.",
            "hi": "Hi {name},",
            "item_label": "Your New Role"
        },
        "status_change": {
            "activated": {
                "subject": "Your LogForge account has been activated",
                "title": "Account Operational",
                "body_text": "It's official! Your LogForge user profile has been fully activated by the administration team. Your workspace is now ready and operational.",
                "footer_text": "The platform's full suite of features is now available to you. Log in today to get started."
            },
            "deactivated": {
                "subject": "Temporary suspension of your LogForge account",
                "title": "Account Suspended",
                "body_text": "Important: We are notifying you that your access to your LogForge account has been temporarily frozen as an administrative or security precaution.",
                "footer_text": "To obtain more information or to restore your access, we strongly recommend contacting your IT supervisor urgently."
            },
            "hi": "Hi {name},"
        },
        "password_reset": {
            "subject": "Reset your LogForge password",
            "title": "Password Reset Request",
            "body_text": "You recently requested to reset your password for your LogForge account. Click the button below to set a new secure password:",
            "cta_label": "Reset My Password",
            "footer_text": "This link will expire in 1 hour. If you did not request a password reset, please ignore this email.",
            "hi": "Hi {name},"
        },
        "agent_key_expiration": {
            "subject": "Imminent expiration of a Docker agent key",
            "title": "Key Expiration Alert",
            "body_text": "We are informing you that the following Docker agent key will expire soon. Once expired, logs sent with this key will be rejected.",
            "hi": "Hi Team,",
            "item_label": "Affected Key",
            "expiration_label": "Expiration Date",
            "remaining_label": "Remaining Time",
            "footer_text": "Please generate a new key in the settings to ensure continued log collection."
        },
        "invitation": {
            "subject": "You've been invited to join {company} on LogForge",
            "badge": "Invitation",
            "title": "You're invited!",
            "body_intro": "{inviter} has invited you to join their workspace on LogForge.",
            "body_sub": "Create your free account by clicking the button below.",
            "joining_label": "You are joining",
            "cta_label": "Accept Invitation",
            "expiry_note": "This link expires in 24 hours. If you weren't expecting this invitation, you can safely ignore this email.",
            "link_fallback": "If the button doesn't work, copy and paste this link into your browser:"
        }
    }
}

async def get_smtp_config(company_id=None):
    """Find the SMTP configuration for a given company (or global if not specified)."""
    query = {"type": "smtp", "enabled": True}
    if company_id:
        query["company_id"] = company_id
    return await db.settings.find_one(query, {"_id": 0})

async def get_app_settings(company_id=None):
    """Get app settings for a given company, falling back to global defaults."""
    if company_id:
        settings = await db.settings.find_one({"type": "app_settings", "company_id": company_id}, {"_id": 0})
    else:
        settings = await db.settings.find_one({"type": "app_settings"}, {"_id": 0})
    if not settings:
        settings = {
            "app_name": "LogForge",
            "primary_color": "#10b981",
            "logo_url": None,
            "language": "en"
        }
    return settings

async def render_template(template_name, context, lang="en", company_id=None):
    """Render a Jinja2 template with common context."""
    app_settings = await get_app_settings(company_id)
    branding = {
        "app_name": app_settings.get('app_name', 'LogForge'),
        "logo_url": app_settings.get('logo_url', ''),
        "primary_color": app_settings.get('primary_color', '#10b981')
    }
    
    # Merge translations
    translations = EMAIL_TRANSLATIONS.get(lang, EMAIL_TRANSLATIONS["en"])
    
    full_context = {
        **context,
        "branding": branding,
        "translations": translations,
        "current_year": datetime.datetime.now().year,
        "base_url": BASE_URL
    }
    
    template = template_env.get_template(template_name)
    return template.render(full_context)

async def send_project_access_email(user_email, user_name, project_name, action="granted", performer_name=None, company_id=None):
    """Send an email when project access is granted or revoked."""
    try:
        app_settings = await get_app_settings(company_id)
        if not app_settings.get('notify_project_access', True):
            return True # Feature disabled by admin

        smtp_config = await get_smtp_config(company_id)
        if not smtp_config:
            return False

        lang = smtp_config.get('language', 'en')
        trans = EMAIL_TRANSLATIONS[lang]["project_access"]
        active_trans = trans[action]

        subject = active_trans["subject"].format(name=project_name)
        title = f"[{EMAIL_TRANSLATIONS[lang]['disclaimer'].split(' ')[-1][:-1]}] {subject}" # Use App Name for prefix
        
        context = {
            "title": active_trans["title"],
            "icon_emoji": "✅" if action == "granted" else "🚫",
            "badge_color": "#10b981" if action == "granted" else "#ef4444",
            "body_intro": trans["hi"].format(name=user_name),
            "body_text": active_trans["body_text"],
            "item_name": project_name,
            "performer_name": performer_name,
            "cta_link": f"{BASE_URL}/projects",
            "translations": {**EMAIL_TRANSLATIONS[lang], **trans, **active_trans},
            "footer_text": active_trans["footer_text"]
        }

        html_body = await render_template("user_action_notification.html", context, lang)
        
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = smtp_config.get('from_email', '')
        msg['To'] = user_email
        msg.set_content(f"{context['body_intro']}\n\n{context['body_text']}\nProject: {project_name}")
        msg.add_alternative(html_body, subtype='html')

        await aiosmtplib.send(msg,
            hostname=smtp_config['host'], port=smtp_config['port'],
            username=smtp_config.get('username', ''), password=smtp_config.get('password', ''),
            use_tls=smtp_config.get('port') == 465, start_tls=smtp_config.get('port') == 587)
        
        return True
    except Exception as e:
        logger.error(f"Failed to send project access email: {e}")
        return False

async def send_permission_change_email(user_email, user_name, permission_key, action="granted", performer_name=None, company_id=None):
    """Send an email when a specific permission is granted or revoked."""
    try:
        app_settings = await get_app_settings(company_id)
        if not app_settings.get('notify_permission_change', True):
            return True # Feature disabled by admin

        smtp_config = await get_smtp_config(company_id)
        if not smtp_config:
            return False

        lang = smtp_config.get('language', 'en')
        trans = EMAIL_TRANSLATIONS[lang]["permission_change"]
        active_trans = trans[action]
        perm_label = trans["labels"].get(permission_key, trans["labels"]["unknown"])

        subject = active_trans["subject"].format(name=perm_label)
        
        context = {
            "title": active_trans["title"],
            "icon_emoji": "🔑" if action == "granted" else "🔒",
            "badge_color": "#3b82f6" if action == "granted" else "#f59e0b",
            "body_intro": trans["hi"].format(name=user_name),
            "body_text": active_trans["body_text"],
            "item_name": perm_label,
            "performer_name": performer_name,
            "cta_link": f"{BASE_URL}/settings",
            "translations": {**EMAIL_TRANSLATIONS[lang], **trans, **active_trans},
            "footer_text": active_trans["footer_text"]
        }

        html_body = await render_template("user_action_notification.html", context, lang)
        
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = smtp_config.get('from_email', '')
        msg['To'] = user_email
        msg.set_content(f"{context['body_intro']}\n\n{context['body_text']}\nPermission: {perm_label}")
        msg.add_alternative(html_body, subtype='html')

        await aiosmtplib.send(msg,
            hostname=smtp_config['host'], port=smtp_config['port'],
            username=smtp_config.get('username', ''), password=smtp_config.get('password', ''),
            use_tls=smtp_config.get('port') == 465, start_tls=smtp_config.get('port') == 587)
        
        return True
    except Exception as e:
        logger.error(f"Failed to send permission change email: {e}")
        return False

async def send_role_change_email(user_email, user_name, new_role, performer_name=None, company_id=None):
    """Send an email when a user's role is updated."""
    try:
        app_settings = await get_app_settings(company_id)
        if not app_settings.get('notify_role_change', True):
            return True

        smtp_config = await get_smtp_config(company_id)
        if not smtp_config: return False

        lang = smtp_config.get('language', 'en')
        trans = EMAIL_TRANSLATIONS[lang]["role_change"]
        role_label = new_role.capitalize()

        subject = trans["subject"].format(role=role_label)
        
        context = {
            "title": trans["title"],
            "icon_emoji": "🎭",
            "badge_color": "#8b5cf6",
            "body_intro": trans["hi"].format(name=user_name),
            "body_text": trans["body_text"],
            "item_name": role_label,
            "performer_name": performer_name,
            "cta_link": f"{BASE_URL}/",
            "translations": {**EMAIL_TRANSLATIONS[lang], **trans},
            "footer_text": trans["footer_text"]
        }

        html_body = await render_template("user_action_notification.html", context, lang)
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = smtp_config.get('from_email', '')
        msg['To'] = user_email
        msg.add_alternative(html_body, subtype='html')

        await aiosmtplib.send(msg, 
            hostname=smtp_config['host'], port=smtp_config['port'],
            username=smtp_config.get('username', ''), password=smtp_config.get('password', ''),
            use_tls=smtp_config.get('port') == 465, start_tls=smtp_config.get('port') == 587)
        return True
    except Exception as e:
        logger.error(f"Failed to send role change email: {e}")
        return False

async def send_status_change_email(user_email, user_name, is_active, performer_name=None, company_id=None):
    """Send an email when account status is toggled."""
    try:
        app_settings = await get_app_settings(company_id)
        if not app_settings.get('notify_status_change', True):
            return True

        smtp_config = await get_smtp_config(company_id)
        if not smtp_config: return False

        lang = smtp_config.get('language', 'en')
        trans = EMAIL_TRANSLATIONS[lang]["status_change"]
        action = "activated" if is_active else "deactivated"
        active_trans = trans[action]

        subject = active_trans["subject"]
        
        context = {
            "title": active_trans["title"],
            "icon_emoji": "✨" if is_active else "🌑",
            "badge_color": "#10b981" if is_active else "#71717a",
            "body_intro": trans["hi"].format(name=user_name),
            "body_text": active_trans["body_text"],
            "item_name": "Account Status: " + action.capitalize(),
            "performer_name": performer_name,
            "cta_link": f"{BASE_URL}/login" if is_active else BASE_URL,
            "translations": {**EMAIL_TRANSLATIONS[lang], **trans, **active_trans},
            "footer_text": active_trans["footer_text"]
        }

        html_body = await render_template("user_action_notification.html", context, lang)
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = smtp_config.get('from_email', '')
        msg['To'] = user_email
        msg.add_alternative(html_body, subtype='html')

        await aiosmtplib.send(msg, 
            hostname=smtp_config['host'], port=smtp_config['port'],
            username=smtp_config.get('username', ''), password=smtp_config.get('password', ''),
            use_tls=smtp_config.get('port') == 465, start_tls=smtp_config.get('port') == 587)
        return True
    except Exception as e:
        logger.error(f"Failed to send status change email: {e}")
        return False

async def send_password_reset_email(user_email, user_name, reset_token, company_id=None):
    """Send an email with a password reset link."""
    try:
        smtp_config = await get_smtp_config(company_id)
        if not smtp_config:
            return False

        lang = smtp_config.get('language', 'en')
        trans = EMAIL_TRANSLATIONS[lang]["password_reset"]
        
        subject = trans["subject"]
        reset_link = f"{BASE_URL}/reset-password?token={reset_token}"
        
        context = {
            "title": trans["title"],
            "icon_emoji": "🔐",
            "badge_color": "#10b981",
            "body_intro": trans["hi"].format(name=user_name),
            "body_text": trans["body_text"],
            "cta_label": trans["cta_label"],
            "cta_link": reset_link,
            "translations": {**EMAIL_TRANSLATIONS[lang], **trans},
            "footer_text": trans["footer_text"]
        }

        html_body = await render_template("password_reset.html", context, lang)
        
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = smtp_config.get('from_email', '')
        msg['To'] = user_email
        msg.set_content(f"{context['body_intro']}\n\n{context['body_text']}\nReset Link: {reset_link}")
        msg.add_alternative(html_body, subtype='html')

        await aiosmtplib.send(msg,
            hostname=smtp_config['host'], port=smtp_config['port'],
            username=smtp_config.get('username', ''), password=smtp_config.get('password', ''),
            use_tls=smtp_config.get('port') == 465, start_tls=smtp_config.get('port') == 587)
        
        return True
    except Exception as e:
        logger.error(f"Failed to send password reset email: {e}")
        return False

async def send_agent_key_expiration_alert(admin_emails, key_description, expires_at, days_left, company_id=None):
    """Send an email to admins when an agent key is about to expire."""
    try:
        smtp_config = await get_smtp_config(company_id)
        if not smtp_config:
            return False

        lang = smtp_config.get('language', 'en')
        trans = EMAIL_TRANSLATIONS[lang]["agent_key_expiration"]
        
        subject = trans["subject"]
        
        context = {
            "title": trans["title"],
            "icon_emoji": "⚠️",
            "badge_color": "#f59e0b",
            "body_intro": trans["hi"],
            "body_text": trans["body_text"],
            "item_name": key_description,
            "expires_at": expires_at,
            "days_left": days_left,
            "cta_link": f"{BASE_URL}/settings",
            "translations": {**EMAIL_TRANSLATIONS[lang], **trans},
            "footer_text": trans["footer_text"]
        }

        html_body = await render_template("agent_key_expiration.html", context, lang)
        
        for email in admin_emails:
            msg = EmailMessage()
            msg['Subject'] = subject
            msg['From'] = smtp_config.get('from_email', '')
            msg['To'] = email
            msg.set_content(f"{context['body_intro']}\n\n{context['body_text']}\nKey: {key_description}\nExpires: {expires_at}")
            msg.add_alternative(html_body, subtype='html')

            await aiosmtplib.send(msg,
                hostname=smtp_config['host'], port=smtp_config['port'],
                username=smtp_config.get('username', ''), password=smtp_config.get('password', ''),
                use_tls=smtp_config.get('port') == 465, start_tls=smtp_config.get('port') == 587)
        
        return True
    except Exception as e:
        logger.error(f"Failed to send agent key expiration alert: {e}")
        return False


async def send_invitation_email(invitee_email: str, company_name: str, token: str, invited_by_name: str, company_id: str = None) -> bool:
    """Send a company invitation email with a signup link."""
    try:
        smtp_config = await get_smtp_config(company_id)
        if not smtp_config:
            return False

        lang = smtp_config.get("language", "en")
        trans = EMAIL_TRANSLATIONS[lang]["invitation"]

        subject = trans["subject"].format(company=company_name)
        invitation_link = f"{BASE_URL}/signup?token={token}"

        context = {
            "invited_by_name": invited_by_name,
            "company_name": company_name,
            "invitation_link": invitation_link,
            "translations": {**EMAIL_TRANSLATIONS[lang], "invitation": trans},
        }

        html_body = await render_template("invitation.html", context, lang)

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = smtp_config.get("from_email", "")
        msg["To"] = invitee_email
        msg.set_content(
            f"{trans['body_intro'].format(inviter=invited_by_name)}\n\n"
            f"{trans['joining_label']}: {company_name}\n\n"
            f"{invitation_link}"
        )
        msg.add_alternative(html_body, subtype="html")

        await aiosmtplib.send(
            msg,
            hostname=smtp_config["host"],
            port=smtp_config["port"],
            username=smtp_config.get("username", ""),
            password=smtp_config.get("password", ""),
            use_tls=smtp_config.get("port") == 465,
            start_tls=smtp_config.get("port") == 587,
        )
        return True
    except Exception as e:
        logger.error(f"Failed to send invitation email to {invitee_email}: {e}")
        return False
