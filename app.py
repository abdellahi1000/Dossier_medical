from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory, send_file
from pymongo import MongoClient
from bson.objectid import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
import random
import string
import hashlib
import re
import secrets
from PIL import Image, ImageDraw, ImageFont
import math
import qrcode
import io
import base64

# Initialize Flask
app = Flask(__name__)
app.config.from_object('config')

# Ensure Upload Folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# MongoDB Connection
client = MongoClient(app.config['MONGO_URI'])
db = client[app.config['DATABASE_NAME']]

@app.route("/test-mongodb")
def test_mongodb():
    try:
        uri = os.getenv("MONGODB_URI")

        if not uri:
            return {"status": "error", "message": "MONGODB_URI is missing"}, 500

        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")

        db = client["Dossier_medical"]

        return {
            "status": "success",
            "mongodb": "connected",
            "database": db.name,
            "collections": db.list_collection_names()
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }, 500

# --- SAFE OBJECTID HELPER ---
def safe_object_id(id_val):
    if not id_val:
        return None
    try:
        return ObjectId(id_val)
    except Exception:
        return id_val

# --- AUDIT LOGGING HELPER ---
def log_audit_action(action, details):
    user_id = session.get('user_id', 'anonymous')
    user_name = session.get('user_name', 'Visiteur anonyme')
    role = session.get('role', 'Visiteur')
    
    log_entry = {
        "timestamp": datetime.now(),
        "user_id": user_id,
        "user_name": user_name,
        "role": role,
        "action": action,
        "details": details,
        "ip_address": request.remote_addr
    }
    db.audit_logs.insert_one(log_entry)

# --- SMS LOGGING HELPER ---
def send_simulated_sms(phone, message):
    sms_entry = {
        "phone": phone,
        "message": message,
        "time": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    }
    db.sms_logs.insert_one(sms_entry)

# --- IMAGE GENERATION HELPER ---
def generate_mock_medical_image(file_path, image_type):
    try:
        # Create a simple PIL image representing a clinical graphic/X-Ray
        img = Image.new('RGB', (600, 450), color='#333333')
        d = ImageDraw.Draw(img)
        # Draw frame
        d.rectangle([(20, 20), (580, 430)], outline='#1B7F3A', width=4)
        # Write text
        d.text((50, 50), f"IMAGERIE MEDICALE : {image_type}", fill='#1B7F3A')
        d.text((50, 90), "Centre: Hospitalier / Clinique", fill='#FFFFFF')
        d.text((50, 130), f"Date d'acquisition: {datetime.now().strftime('%d/%m/%Y %H:%M')}", fill='#FFFFFF')
        
        # Draw simple waves to represent ECG or structure
        points = [(50 + i*10, 250 + int(random.choice([-40, -20, 0, 20, 40]))) for i in range(50)]
        for i in range(len(points)-1):
            d.line([points[i], points[i+1]], fill='#2E9E4D', width=2)
            
        d.text((50, 380), "DOCUMENT ORIGINAL CONSERVE EN QUALITE D'ORIGINE", fill='#F4C542')
        img.save(file_path)
    except Exception as e:
        print(f"PIL Image creation failed: {e}")
        # Write plain text fallback
        with open(file_path, 'w') as f:
            f.write(f"Mock clinical image: {image_type} - Quality preserved.")

# --- FILE ARCHIVE DIRECTORY STRUCTURE HELPER ---
def create_patient_archive(patient, centre, reservation, exam_category):
    # Sanitize inputs
    def sanitize(text):
        return re.sub(r'[\\/*?:"<>|]', "", text).replace(" ", "_")
        
    date_str = reservation['date_reservation'].strftime('%d-%m-%Y')
    time_str = reservation['date_reservation'].strftime('%Hh%M')
    
    centre_name = sanitize(centre['nom_centre'])
    category_name = sanitize(exam_category)
    patient_dir_name = f"Patient_{patient['numero_national']}"
    folder_name = f"{centre_name}_{date_str}_{time_str}_{category_name}"
    
    # Path construction
    base_path = os.path.join(app.config['UPLOAD_FOLDER'], patient_dir_name, folder_name)
    results_path = os.path.join(base_path, f"Resultats_{category_name}")
    
    os.makedirs(base_path, exist_ok=True)
    os.makedirs(results_path, exist_ok=True)
    
    return base_path, results_path

# Helper to check allowed files
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# --- MEDICAL IMAGING FOLDER HELPER ---
# Exam categories that produce medical images (as opposed to lab/blood analyses).
IMAGING_EXAM_CATEGORIES = {"Radiologie", "Cardiologie", "Imagerie Médicale"}

def is_imaging_exam_category(category):
    return category in IMAGING_EXAM_CATEGORIES

def get_images_medicales_dir(results_dir, exam_category):
    """Returns (and ensures) the dedicated 'Images médicales' subfolder for imaging
    exams, kept separate from the final medical report. Only created for imaging
    categories — lab/blood-analysis exams never get this subfolder."""
    if not is_imaging_exam_category(exam_category):
        return None
    images_dir = os.path.join(results_dir, "Images_medicales")
    os.makedirs(images_dir, exist_ok=True)
    return images_dir

# --- SPECIALTY / EXAM CATEGORY MATCHING HELPERS ---
def get_exam_categories():
    """Distinct exam categories from the catalog — the single source of truth
    a doctor's specialty must match to be assignable to a given exam."""
    return sorted(db.examens.distinct("categorie"))

def exam_categories_for_examens(examens):
    """Distinct categories among a list of exam documents."""
    return sorted({ex.get('categorie', '') for ex in examens if ex.get('categorie')})

# --- CNAM FICHE HELPERS ---
def default_cnam_fiche_fields(reservation):
    """Default clinical/administrative fields shared by every CNAM Fiche,
    created once at first reservation and updated in place afterwards."""
    return {
        "date_soins": reservation.get('date_reservation', datetime.now()),
        "prescriptions": [],
        "analyses": [],
        "observations_medecin": "",
        "medecin_updated_at": None,
        "id_medecin_editeur": None,
        "date_entree": None,
        "date_sortie": None,
        "montant_facture": None,
        "montant_rembourse_cnam": None,
        "notes_admin": "",
        "valide_administration": False,
        "admin_updated_at": None,
        "id_admin_validateur": None,
        "updated_at": datetime.now()
    }

def generate_qr_base64(data):
    """Generate a small QR code PNG for `data`, returned as a base64 data URI."""
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    encoded = base64.b64encode(buf.getvalue()).decode('ascii')
    return f"data:image/png;base64,{encoded}"

def get_doctor_signature_data_uri(medecin):
    """Read a doctor's stored handwritten-signature image straight off disk and
    return it as a base64 data URI, for inlining into generated documents.
    Never exposed via a public URL — read server-side only."""
    path = medecin.get('signature_image_path') if medecin else None
    if not path or not os.path.exists(path):
        return None
    ext = os.path.splitext(path)[1].lstrip('.').lower()
    mime = 'image/png' if ext == 'png' else 'image/jpeg'
    with open(path, 'rb') as f:
        encoded = base64.b64encode(f.read()).decode('ascii')
    return f"data:{mime};base64,{encoded}"

def _asset_data_uri(path):
    """Generic file-on-disk -> base64 data URI helper, shared by the hospital
    logo/stamp/director-signature helpers below."""
    if not path or not os.path.exists(path):
        return None
    ext = os.path.splitext(path)[1].lstrip('.').lower()
    mime = 'image/png' if ext == 'png' else 'image/jpeg'
    with open(path, 'rb') as f:
        encoded = base64.b64encode(f.read()).decode('ascii')
    return f"data:{mime};base64,{encoded}"

def get_hospital_logo_data_uri(centre):
    return _asset_data_uri(centre.get('logo_image_path')) if centre else None

def get_hospital_stamp_data_uri(centre):
    return _asset_data_uri(centre.get('stamp_image_path')) if centre else None

def get_hospital_director_signature_data_uri(centre):
    return _asset_data_uri(centre.get('director_signature_image_path')) if centre else None

@app.context_processor
def inject_nav_branding():
    """Global vs. hospital-scoped navbar branding: KHIDMATY everywhere except
    inside a logged-in hospital admin's own workspace, where the hospital's
    own logo/name take over (dynamically, straight from its centre document)."""
    if session.get('role') == 'admin':
        admin = db.administrations.find_one({"_id": safe_object_id(session.get('user_id'))})
        centre = db.centres.find_one({"nom_centre": admin.get('centre_medical')}) if admin else None
        if centre:
            return {
                'nav_logo_url': get_hospital_logo_data_uri(centre),
                'nav_brand_text': 'Gestion des Dossiers Médicaux'
            }
    return {
        'nav_logo_url': None,
        'nav_brand_text': 'KHIDMATY – Gestion des Dossiers Médicaux'
    }


# --- DATABASE SEEDER ---
def seed_database():
    # 1. Seed Centers
    if db.centres.count_documents({}) == 0:
        centres = [
            {
                "nom_centre": "CENTRE MÉDICAL CHIFFA",
                "adresse": "Avenue Gamal Abdel Nasser, Nouakchott",
                "telephone": "2222-3333"
            },
            {
                "nom_centre": "CENTRE MÉDICAL EL AMAL",
                "adresse": "Tevragh Zeina, Nouakchott",
                "telephone": "4444-5555"
            }
        ]
        db.centres.insert_many(centres)
        print("Medical centers seeded.")

    # 2. Seed Exams
    if db.examens.count_documents({}) == 0:
        examens = [
            {
                "type_examen": "Analyse Sanguine - VIH",
                "categorie": "Analyse Sanguine",
                "prix": 500.0
            },
            {
                "type_examen": "Analyse Sanguine - FLH",
                "categorie": "Analyse Sanguine",
                "prix": 800.0
            },
            {
                "type_examen": "Radiologie Thoracique",
                "categorie": "Radiologie",
                "prix": 1200.0
            },
            {
                "type_examen": "Échographie Abdominale",
                "categorie": "Radiologie",
                "prix": 1500.0
            }
        ]
        db.examens.insert_many(examens)
        print("Exams Catalog seeded.")

    # 4. Seed Administrators
    if db.administrations.count_documents({}) == 0:
        admins = [
            {
                "_id": "admin_sidy",
                "nom": "Sidy",
                "prenom": "Diallo",
                "password": generate_password_hash("admin")
            }
        ]
        for a in admins:
            db.administrations.replace_one({"_id": a["_id"]}, a, upsert=True)
        print("Administrators seeded.")

    # 5. Seed Plannings (Time Slots) and Groupes per Hospital
    if db.plannings.count_documents({}) == 0 and db.centres.count_documents({}) > 0:
        centres = list(db.centres.find({}))
        from datetime import timedelta
        
        for c in centres:
            for day_offset in range(15):
                date_str = (datetime.now() + timedelta(days=day_offset)).strftime('%Y-%m-%d')
                plannings = [
                    {"id_centre": c["_id"], "date": date_str, "start_time": "08:00", "end_time": "09:00", "is_active": True},
                    {"id_centre": c["_id"], "date": date_str, "start_time": "09:00", "end_time": "10:00", "is_active": True},
                    {"id_centre": c["_id"], "date": date_str, "start_time": "10:30", "end_time": "11:30", "is_active": True},
                    {"id_centre": c["_id"], "date": date_str, "start_time": "14:00", "end_time": "15:00", "is_active": True}
                ]
                res = db.plannings.insert_many(plannings)
                
                # Create corresponding groups
                groupes = [
                    {"id_centre": c["_id"], "id_planning": res.inserted_ids[0], "date": date_str, "nom_groupe": "J1", "max_patients": 10},
                    {"id_centre": c["_id"], "id_planning": res.inserted_ids[1], "date": date_str, "nom_groupe": "J2", "max_patients": 15},
                    {"id_centre": c["_id"], "id_planning": res.inserted_ids[2], "date": date_str, "nom_groupe": "J3", "max_patients": 10},
                    {"id_centre": c["_id"], "id_planning": res.inserted_ids[3], "date": date_str, "nom_groupe": "J4", "max_patients": 15}
                ]
                db.groupes.insert_many(groupes)
            
        print("Plannings and Groupes seeded.")

try:
    seed_database()
except Exception as e:
    print(f"Seeding error (make sure MongoDB is running): {e}")


# --- GENERAL ROUTES & AUTH ---
@app.route('/')
def index():
    if 'role' in session:
        if session['role'] == 'patient':
            return redirect(url_for('patient_dashboard'))
        elif session['role'] == 'medecin':
            return redirect(url_for('doctor_dashboard'))
        elif session['role'] == 'admin':
            return redirect(url_for('admin_dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        # Try patient first (by numero_national)
        user = db.patients.find_one({"numero_national": username})
        if user and check_password_hash(user['password'], password):
            session['user_id'] = str(user['_id'])
            session['user_name'] = f"{user['prenom']} {user['nom']}"
            session['role'] = 'patient'
            log_audit_action("Connexion", "Patient connecté avec succès")
            flash('Connexion réussie !', 'success')
            return redirect(url_for('patient_dashboard'))
        
        # Try medecin (by numero_nni)
        user = db.medecins.find_one({"numero_nni": username})
        if not user:
            user = db.medecins.find_one({"nom": username})
        if user and check_password_hash(user['password'], password):
            session['user_id'] = str(user['_id'])
            session['user_name'] = f"{user['prenom']} {user['nom']}"
            session['role'] = 'medecin'
            session['specialite'] = user.get('specialite', '')
            log_audit_action("Connexion", "Médecin connecté avec succès")
            flash('Connexion Médecin réussie !', 'success')
            return redirect(url_for('doctor_dashboard'))
        
        # Try admin (by identifiant_administratif)
        user = db.administrations.find_one({"identifiant_administratif": username})
        if not user:
            user = db.administrations.find_one({"nom": username})
        if user and check_password_hash(user['password'], password):
            session['user_id'] = str(user['_id'])
            session['user_name'] = f"{user['prenom']} {user['nom']}"
            session['role'] = 'admin'
            session['centre_name'] = user.get('centre_medical', '')
            log_audit_action("Connexion", "Administrateur connecté avec succès")
            flash('Connexion Hôpital réussie !', 'success')
            return redirect(url_for('admin_dashboard'))
                
        log_audit_action("Echec Connexion", f"Tentative infructueuse, ID: {username}")
        flash('Identifiants incorrects.', 'error')
        return redirect(url_for('login'))
        
    return render_template('login.html')

def _find_account_by_identifiant(identifiant):
    """Same cross-collection lookup used at login: patient by numero_national,
    médecin by numero_nni/nom, admin by identifiant_administratif/nom.
    Returns (collection, user_doc) or (None, None)."""
    user = db.patients.find_one({"numero_national": identifiant})
    if user:
        return db.patients, user
    user = db.medecins.find_one({"numero_nni": identifiant}) or db.medecins.find_one({"nom": identifiant})
    if user:
        return db.medecins, user
    user = db.administrations.find_one({"identifiant_administratif": identifiant}) or db.administrations.find_one({"nom": identifiant})
    if user:
        return db.administrations, user
    return None, None

def _send_reset_code(identifiant):
    """Generate + store a fresh OTP for the account matching `identifiant`,
    and simulate sending it (used by both the initial request and Resend)."""
    collection, user = _find_account_by_identifiant(identifiant)
    if not user:
        return None
    reset_code = f"{secrets.randbelow(1000000):06d}"
    collection.update_one(
        {"_id": user['_id']},
        {"$set": {
            "reset_code": reset_code,
            "reset_code_expires": datetime.now() + timedelta(minutes=15)
        }}
    )
    phone = user.get('telephone')
    if phone:
        send_simulated_sms(phone, f"Votre code de réinitialisation de mot de passe : {reset_code} (valable 15 minutes).")
    log_audit_action("Mot de Passe Oublié", f"Code de réinitialisation généré pour {identifiant}")
    return reset_code

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        identifiant = request.form.get('identifiant', '').strip()
        reset_code = _send_reset_code(identifiant)

        if reset_code:
            flash("Un code de réinitialisation a été généré. Comme ceci est un environnement de démonstration (aucune passerelle SMS/e-mail réelle n'est connectée), le code s'affiche directement ci-dessous.", "success")
            return render_template('reset_password.html', identifiant=identifiant, demo_code=reset_code, otp_verified=False)

        # Same generic outcome whether or not the account exists, to avoid confirming/denying accounts.
        flash("Si un compte correspond à cet identifiant, un code de réinitialisation a été généré.", "info")
        return redirect(url_for('forgot_password'))

    return render_template('forgot_password.html')

@app.route('/resend_otp', methods=['POST'])
def resend_otp():
    identifiant = request.form.get('identifiant', '').strip()
    reset_code = _send_reset_code(identifiant)
    if reset_code:
        flash("Un nouveau code a été généré et s'affiche ci-dessous.", "success")
        return render_template('reset_password.html', identifiant=identifiant, demo_code=reset_code, otp_verified=False)

    flash("Si un compte correspond à cet identifiant, un nouveau code a été généré.", "info")
    return redirect(url_for('forgot_password'))

@app.route('/verify_otp', methods=['POST'])
def verify_otp():
    identifiant = request.form.get('identifiant', '').strip()
    code = request.form.get('code', '').strip()

    collection, user = _find_account_by_identifiant(identifiant)
    if (not user or not user.get('reset_code') or user['reset_code'] != code
            or not user.get('reset_code_expires') or user['reset_code_expires'] < datetime.now()):
        flash("Code OTP invalide. Veuillez réessayer.", "error")
        return render_template('reset_password.html', identifiant=identifiant, otp_verified=False)

    # Server-side gate: only a verified identifiant may reach the password step.
    session['otp_verified_for'] = identifiant
    flash("Code vérifié. Choisissez votre nouveau mot de passe.", "success")
    return render_template('reset_password.html', identifiant=identifiant, otp_verified=True)

@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        identifiant = request.form.get('identifiant', '').strip()
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        # The password step is only reachable after /verify_otp confirmed this
        # exact identifiant server-side — never trust the client's form state alone.
        if session.get('otp_verified_for') != identifiant:
            flash("Veuillez d'abord vérifier votre code OTP.", "error")
            return redirect(url_for('forgot_password'))

        weak_passwords = ['12345678', '123456789', 'password', '11111111', 'qwerty', 'abc12345']
        if len(new_password) < 8 or new_password.lower() in weak_passwords:
            flash('Le mot de passe est trop faible. Choisissez au moins 8 caractères.', 'error')
            return render_template('reset_password.html', identifiant=identifiant, otp_verified=True)
        if new_password != confirm_password:
            flash('Les mots de passe ne correspondent pas.', 'error')
            return render_template('reset_password.html', identifiant=identifiant, otp_verified=True)

        collection, user = _find_account_by_identifiant(identifiant)
        if not user:
            flash('Compte introuvable.', 'error')
            return redirect(url_for('forgot_password'))

        collection.update_one(
            {"_id": user['_id']},
            {"$set": {"password": generate_password_hash(new_password)},
             "$unset": {"reset_code": "", "reset_code_expires": ""}}
        )
        session.pop('otp_verified_for', None)
        log_audit_action("Réinitialisation Mot de Passe", f"Mot de passe réinitialisé pour {identifiant}")
        flash('Mot de passe réinitialisé avec succès. Vous pouvez vous connecter.', 'success')
        return redirect(url_for('login'))

    identifiant = request.args.get('identifiant', '')
    return render_template('reset_password.html', identifiant=identifiant, otp_verified=False)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template('register.html')
    
    # POST: handle registration for all 3 roles
    role = request.form.get('role', 'patient')
    password = request.form.get('password', '')
    
    weak_passwords = ['12345678', '123456789', 'password', '11111111', 'qwerty', 'abc12345']
    if len(password) < 8 or password.lower() in weak_passwords:
        flash('Your password is too weak. Please choose a stronger password with at least 8 characters.', 'error')
        return redirect(url_for('register'))
    
    if role == 'patient':
        nom = request.form.get('nom', '').strip()
        prenom = request.form.get('prenom', '').strip()
        numero_national = request.form.get('numero_national', '').strip()
        telephone = request.form.get('telephone', '').strip()
        
        existing = db.patients.find_one({"numero_national": numero_national})
        if existing:
            flash('Un patient avec ce numéro national existe déjà.', 'error')
            return redirect(url_for('register'))
        
        patient = {
            "nom": nom,
            "prenom": prenom,
            "numero_national": numero_national,
            "telephone": telephone,
            "date_inscription": datetime.now(),
            "password": generate_password_hash(password)
        }
        result = db.patients.insert_one(patient)
        session['user_id'] = str(result.inserted_id)
        session['user_name'] = f"{prenom} {nom}"
        session['role'] = 'patient'
        log_audit_action("Inscription", "Nouveau patient inscrit avec succès")
        flash('Inscription réussie ! Bienvenue.', 'success')
        return redirect(url_for('patient_dashboard'))
        

    elif role == 'admin':
        nom = request.form.get('nom_adm', '').strip()
        prenom = request.form.get('prenom_adm', '').strip()
        identifiant_administratif = request.form.get('identifiant_administratif', '').strip()
        centre_medical = request.form.get('centre_medical_adm', '').strip().upper()
        
        existing = db.administrations.find_one({"identifiant_administratif": identifiant_administratif})
        if existing:
            flash('Un administrateur avec cet identifiant existe déjà.', 'error')
            return redirect(url_for('register'))
        
        admin = {
            "nom": nom,
            "prenom": prenom,
            "identifiant_administratif": identifiant_administratif,
            "centre_medical": centre_medical,
            "date_inscription": datetime.now(),
            "password": generate_password_hash(password)
        }
        result = db.administrations.insert_one(admin)
        
        # Add to db.centres if not exists
        if not db.centres.find_one({"nom_centre": centre_medical}):
            db.centres.insert_one({
                "nom_centre": centre_medical,
                "adresse": "—",
                "telephone": "—"
            })
            
        session['user_id'] = str(result.inserted_id)
        session['user_name'] = f"{prenom} {nom}"
        session['role'] = 'admin'
        session['centre_name'] = centre_medical
        log_audit_action("Inscription", "Nouvel administrateur inscrit avec succès")
        flash('Inscription Hôpital réussie !', 'success')
        return redirect(url_for('admin_dashboard'))
    
    flash('Type de compte invalide.', 'error')
    return redirect(url_for('register'))

@app.route('/logout')
def logout():
    log_audit_action("Déconnexion", "Session utilisateur fermée")
    session.clear()
    flash('Vous avez été déconnecté.', 'info')
    return redirect(url_for('login'))


# --- PATIENT WORKFLOWS ---
@app.route('/patient/dashboard')
def patient_dashboard():
    if 'role' not in session or session['role'] != 'patient':
        return redirect(url_for('login'))
        
    patient = db.patients.find_one({"_id": safe_object_id(session['user_id'])})
    reservations = list(db.reservations.find({"id_patient": safe_object_id(session['user_id'])}).sort("date_reservation", -1))
    
    for res in reservations:
        res['centre'] = db.centres.find_one({"_id": ObjectId(res['id_centre'])})
        res['examens'] = list(db.examens.find({"_id": {"$in": [ObjectId(e) for e in res.get('examens_ids', [])]}}))
        res['paiement'] = db.paiements.find_one({"id_reservation": res['_id']})
        if res['paiement'] and res['paiement']['mode_paiement'] == 'CNAM':
            res['cnam'] = db.cnam.find_one({"id_reservation": res['_id']})
            
    last_centre = None
    if reservations and reservations[0].get('centre'):
        last_centre = reservations[0]['centre']
            
    return render_template('patient_dashboard.html', 
                           patient=patient, 
                           reservations=reservations,
                           last_centre=last_centre)

@app.route('/patient/book_step1')
def book_exam_step1():
    if 'role' not in session or session['role'] != 'patient':
        return redirect(url_for('login'))
        
    patient = db.patients.find_one({"_id": safe_object_id(session['user_id'])})
    centres = list(db.centres.find())
    examens = list(db.examens.find())
    
    examens_by_category = {}
    for ex in examens:
        cat = ex.get('categorie', 'Autre')
        if cat not in examens_by_category:
            examens_by_category[cat] = []
        ex_dict = dict(ex)
        ex_dict['_id'] = str(ex['_id'])
        examens_by_category[cat].append(ex_dict)
            
    return render_template('book_exam_step1.html', 
                           patient=patient, 
                           centres=centres,
                           examens_by_category=examens_by_category)

@app.route('/patient/book', methods=['GET', 'POST'])
def book_exam():
    if 'role' not in session or session['role'] != 'patient':
        return redirect(url_for('login'))
        
    patient = db.patients.find_one({"_id": safe_object_id(session['user_id'])})

    if request.method == 'GET':
        id_centre = request.args.get('id_centre')
        category = request.args.get('category')
        appointment_date = request.args.get('appointment_date')
        id_slot = request.args.get('id_slot')
        
        # If parameters are missing, redirect back to step 1
        if not id_centre or not category or not appointment_date or not id_slot:
            flash("Veuillez commencer la réservation depuis votre espace en sélectionnant un créneau.", "info")
            return redirect(url_for('book_exam_step1'))
            
        centre = db.centres.find_one({"_id": ObjectId(id_centre)})
        slot = db.plannings.find_one({"_id": ObjectId(id_slot)})
        if slot:
            slot['label'] = f"{slot['start_time']} - {slot['end_time']}"
        # Get all exams available for the chosen category
        available_exams = list(db.examens.find({"categorie": category}))
        
        payment_address = "PAY-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
        
        return render_template('book_exam.html', 
                               patient=patient, 
                               centre=centre, 
                               category=category, 
                               appointment_date=appointment_date, 
                               slot=slot, 
                               available_exams=available_exams,
                               payment_address=payment_address)
                               
    if request.method == 'POST':
        id_centre = request.form.get('id_centre')
        exam_ids = request.form.getlist('exams')
        id_slot = request.form.get('id_slot')
        montant_total = float(request.form.get('montant_total'))
        mode_paiement = request.form.get('mode_paiement')
        appointment_date = request.form.get('appointment_date')
        
        if not id_centre or not exam_ids or not id_slot:
            flash('Veuillez remplir correctement toutes les étapes de la réservation.', 'error')
            return redirect(url_for('patient_dashboard'))
            
        # 1. Fetch & Verify Time Slot Capacity
        slot_doc = db.plannings.find_one({"_id": ObjectId(id_slot)})
        if not slot_doc:
            flash('Créneau horaire introuvable.', 'error')
            return redirect(url_for('book_exam'))
            
        groupe_doc = db.groupes.find_one({"id_planning": slot_doc['_id']})
        if not groupe_doc:
            flash('Ce créneau n\'a pas de groupe assigné.', 'error')
            return redirect(url_for('book_exam'))
            
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999)
        
        slot_count = db.reservations.count_documents({
            "id_slot": ObjectId(id_slot),
            "date_reservation": {"$gte": today_start, "$lte": today_end}
        })
        
        if slot_count >= groupe_doc['max_patients']:
            flash("Ce créneau horaire est complet. Veuillez en choisir un autre.", "error")
            return redirect(url_for('book_exam'))
            
        # 2. Generate Codes
        code_secret = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        numero_group = groupe_doc['nom_groupe']
        numero_appel = slot_count + 1
        
        total_bookings = db.reservations.count_documents({})
        numero_de_rendez_vous = total_bookings + 1
        
        first_exam = db.examens.find_one({"_id": ObjectId(exam_ids[0])})
        lettre = 'A' if first_exam and first_exam['categorie'] == 'Analyse Sanguine' else 'R'
        
        # 3. Create Reservation Document
        reservation = {
            "id_patient": safe_object_id(session['user_id']),
            "id_centre": ObjectId(id_centre),
            "examens_ids": [ObjectId(eid) for eid in exam_ids],
            "id_slot": ObjectId(id_slot),
            "date_reservation": datetime.now(),
            "montant_total": montant_total,
            "code_secret": code_secret,
            "numero_appel": numero_appel,
            "lettre": lettre,
            "numero_group": numero_group,
            "numero_de_rendez_vous": numero_de_rendez_vous,
            "statut": "En attente de validation"
        }
        res_result = db.reservations.insert_one(reservation)
        reservation_id = res_result.inserted_id
        
        # 4. Handle Payment
        # Calculate CNAM coverage and patient contribution if CNAM payment
        payment_address = request.form.get('payment_address')
        if mode_paiement == 'CNAM':
            payment_address = request.form.get('payment_address_cnam')
            
        payment_proof_path = None
        if mode_paiement == 'Normal':
            payment_proof_file = request.files.get('payment_proof')
            if payment_proof_file and payment_proof_file.filename != '':
                centre_doc = db.centres.find_one({"_id": ObjectId(id_centre)})
                base_dir, _ = create_patient_archive(patient, centre_doc, reservation, first_exam['categorie'])
                filename = secure_filename(f"payment_proof_{code_secret}." + payment_proof_file.filename.rsplit('.', 1)[-1].lower())
                filepath = os.path.join(base_dir, filename)
                payment_proof_file.save(filepath)
                payment_proof_path = filepath
                
        cnam_rate = app.config['CNAM_COVERAGE_RATE']
        cnam_amount = round(montant_total * cnam_rate, 2) if mode_paiement == 'CNAM' else 0
        patient_amount = round(montant_total * (1 - cnam_rate), 2) if mode_paiement == 'CNAM' else montant_total
        paiement = {
            "id_reservation": reservation_id,
            "montant_total": montant_total,
            "mode_paiement": mode_paiement,
            "cnam_amount": cnam_amount,
            "patient_amount": patient_amount,
            "date_paiement": datetime.now(),
            "statut_paiement": "Validé" if mode_paiement == "Normal" else "En attente",
            "payment_address": payment_address,
            "payment_proof_path": payment_proof_path
        }
        db.paiements.insert_one(paiement)
        
        # 5. Handle CNAM Cover & File Upload
        cni_paths = []
        if mode_paiement == 'CNAM':
            # Use NNI as CNAM number from patient record
            numero_cnam = patient.get('numero_national')
            type_couverture = request.form.get('type_couverture')
            date_expiration = request.form.get('date_expiration')
            ayant_droit = request.form.get('ayant_droit')
            relation = request.form.get('relation', 'Bénéficiaire principal')
            
            # Simple CNAM card expiration validation
            try:
                exp_date = datetime.strptime(date_expiration, '%Y-%m-%d')
                if exp_date < datetime.now():
                    # Expired CNAM card
                    db.reservations.update_one({"_id": reservation_id}, {"$set": {"statut": "Annulé (CNAM Expirée)"}})
                    db.paiements.update_one({"id_reservation": reservation_id}, {"$set": {"statut_paiement": "Refusé"}})
                    log_audit_action("Refus CNAM", f"Carte CNAM expirée pour la réservation #{numero_de_rendez_vous}")
                    flash("Votre carte CNAM est expirée. Réservation annulée. Veuillez repasser en Paiement Normal.", "error")
                    return redirect(url_for('book_exam'))
            except Exception:
                pass
                
            # Create CNAM document in DB
            cnam_entry = {
                "id_reservation": reservation_id,
                "numero_cnam": numero_cnam,
                "date_expiration": date_expiration,
                "type_couverture": type_couverture,
                "statut": "En attente",
                "ayant_droit": ayant_droit,
                "relation": relation,
                "cni_files": []
            }
            cnam_entry.update(default_cnam_fiche_fields(reservation))
            
            # Perform Archive Directories Setup early to place CNI uploads
            centre_doc = db.centres.find_one({"_id": ObjectId(id_centre)})
            base_dir, results_dir = create_patient_archive(patient, centre_doc, reservation, first_exam['categorie'])
            
            # Process uploaded CNI images
            cni_format = request.form.get('cni_format', 'unique')
            if cni_format == 'unique':
                file = request.files.get('cni_unique')
                if file and file.filename != '' and allowed_file(file.filename):
                    filename = secure_filename(f"cni_recto_verso_{code_secret}." + file.filename.rsplit('.', 1)[1].lower())
                    filepath = os.path.join(base_dir, filename)
                    file.save(filepath)
                    cni_paths.append(filepath)
            else:
                file_recto = request.files.get('cni_recto')
                file_verso = request.files.get('cni_verso')
                if file_recto and file_recto.filename != '' and allowed_file(file_recto.filename):
                    fn_recto = secure_filename(f"cni_recto_{code_secret}." + file_recto.filename.rsplit('.', 1)[1].lower())
                    fp_recto = os.path.join(base_dir, fn_recto)
                    file_recto.save(fp_recto)
                    cni_paths.append(fp_recto)
                if file_verso and file_verso.filename != '' and allowed_file(file_verso.filename):
                    fn_verso = secure_filename(f"cni_verso_{code_secret}." + file_verso.filename.rsplit('.', 1)[1].lower())
                    fp_verso = os.path.join(base_dir, fn_verso)
                    file_verso.save(fp_verso)
                    cni_paths.append(fp_verso)
            
            cnam_entry["cni_files"] = cni_paths
            db.cnam.insert_one(cnam_entry)
        else:
            # Payment Normal
            centre_doc = db.centres.find_one({"_id": ObjectId(id_centre)})
            base_dir, results_dir = create_patient_archive(patient, centre_doc, reservation, first_exam['categorie'])

            # Every reservation gets a shared CNAM Fiche, even outside CNAM payment mode
            fiche_entry = {"id_reservation": reservation_id}
            fiche_entry.update(default_cnam_fiche_fields(reservation))
            db.cnam.insert_one(fiche_entry)

        # 6. Create Receipt Document (Recu)
        recu = {
            "id_reservation": reservation_id,
            "date_generation": datetime.now(),
            "numero_group": numero_group,
            "numero_de_rendez_vous": numero_de_rendez_vous,
            "code_secret": code_secret
        }
        db.recus.insert_one(recu)
        
        # 7. Write Physical Receipt to Filesystem
        receipt_filepath = os.path.join(base_dir, f"recu_{code_secret}.txt")
        with open(receipt_filepath, 'w', encoding='utf-8') as rf:
            rf.write(f"=== RECU NUMERIQUE - {centre_doc['nom_centre']} ===\n")
            rf.write(f"Rendez-vous: #{numero_de_rendez_vous} | Groupe: {numero_group} | Appel: {lettre}{numero_appel}\n")
            rf.write(f"Patient: {patient['prenom']} {patient['nom']} ({patient['numero_national']})\n")
            rf.write(f"Montant Paye: {montant_total} MRU (Mode: {mode_paiement})\n")
            rf.write(f"Date: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n")
            rf.write(f"Code Secret Patient: {code_secret}\n")
            
        # Also create a clinical center info sheet
        sheet_path = os.path.join(base_dir, "document_officiel_centre.txt")
        with open(sheet_path, 'w', encoding='utf-8') as sf:
            sf.write(f"Fiche Administrative Centre: {centre_doc['nom_centre']}\n")
            sf.write(f"Adresse: {centre_doc['adresse']}\n")
            sf.write(f"Telephone: {centre_doc['telephone']}\n")
            
        # 8. Trigger Simulated SMS Notification
        sms_text = (
            f"Réservation confirmée au {centre_doc['nom_centre']}. "
            f"RDV le {datetime.now().strftime('%d/%m/%Y')} (Créneau: {slot_doc.get('start_time','')} - {slot_doc.get('end_time','')}). "
            f"Code secret: {code_secret}, Groupe: {numero_group}, Appel: {lettre}{numero_appel}."
        )
        send_simulated_sms(patient['telephone'], sms_text)
        
        log_audit_action("Reservation", f"Patient #{patient['numero_national']} a reserve le ticket #{numero_de_rendez_vous}")
        
        flash('Votre réservation a été enregistrée avec succès. Veuillez patienter quelques instants. Un SMS contenant toutes les informations de votre rendez-vous vous sera envoyé automatiquement.', 'success')
        return redirect(url_for('patient_dashboard'))
        
    centres = list(db.centres.find({}))
    examens = list(db.examens.find({}))
    return render_template('book_exam.html', patient=patient, centres=centres, examens=examens)

@app.route('/api/slots')
def api_slots():
    id_centre = request.args.get('id_centre')
    date_str = request.args.get('date')
    
    if not id_centre or not date_str:
        return jsonify([])
        
    plannings = list(db.plannings.find({
        "id_centre": ObjectId(id_centre), 
        "date": date_str,
        "is_active": True
    }).sort("start_time", 1))
    
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        target_date = datetime.now()
        
    today_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = target_date.replace(hour=23, minute=59, second=59, microsecond=999)
    
    result_slots = []
    for plan in plannings:
        groupe = db.groupes.find_one({"id_planning": plan['_id']})
        if not groupe:
            continue
            
        count = db.reservations.count_documents({
            "id_slot": plan['_id'],
            "date_reservation": {"$gte": today_start, "$lte": today_end}
        })
        
        result_slots.append({
            "id": str(plan['_id']),
            "label": f"{plan['start_time']} - {plan['end_time']}",
            "group_number": groupe['nom_groupe'],
            "capacite_max": groupe['max_patients'],
            "current_count": count,
            "disponible": count < groupe['max_patients']
        })
    return jsonify(result_slots)


# --- DOCTOR WORKFLOWS ---
@app.route('/doctor/dashboard')
def doctor_dashboard():
    if 'role' not in session or session['role'] != 'medecin':
        return redirect(url_for('login'))
        
    medecin = db.medecins.find_one({"_id": safe_object_id(session['user_id'])})

    search_code = request.args.get('code_secret', '').strip()
    query = {
        "id_medecin_assigne": safe_object_id(session['user_id']),
        "statut": {"$in": ["Validé par Admin / En attente d'examen", "Saisi", "Signé par Médecin"]}
    }
    if search_code:
        query['code_secret'] = {"$regex": search_code, "$options": "i"}
        
    reservations = list(db.reservations.find(query))
    
    for res in reservations:
        res['patient'] = db.patients.find_one({"_id": res['id_patient']})
        res['examens'] = list(db.examens.find({"_id": {"$in": res['examens_ids']}}))
        res['resultat'] = db.resultats.find_one({"id_reservation": res['_id']})
        
    return render_template('doctor_dashboard.html', medecin=medecin, reservations=reservations)

# --- DOCTOR PROFILE & HANDWRITTEN SIGNATURE IMPORT ---
SIGNATURE_ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

@app.route('/doctor/profile')
def doctor_profile():
    if 'role' not in session or session['role'] != 'medecin':
        return redirect(url_for('login'))

    medecin = db.medecins.find_one({"_id": safe_object_id(session['user_id'])})
    medecin_id = safe_object_id(session['user_id'])

    reservations = list(db.reservations.find({"id_medecin_assigne": medecin_id}))

    pending_admin_validation = []
    validated_by_admin = []
    for res in reservations:
        resultat = db.resultats.find_one({"id_reservation": res['_id']})
        if not resultat:
            continue  # Saisie des résultats not done yet — not part of either list
        res['patient'] = db.patients.find_one({"_id": res['id_patient']})
        res['resultat'] = resultat
        if resultat.get('statut_resultat') == 'Validé par Administration':
            validated_by_admin.append(res)
        else:
            pending_admin_validation.append(res)

    return render_template(
        'doctor_profile.html',
        medecin=medecin,
        pending_admin_validation=pending_admin_validation,
        validated_by_admin=validated_by_admin
    )

@app.route('/doctor/profile/signature/upload', methods=['POST'])
def doctor_upload_signature():
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if 'role' not in session or session['role'] != 'medecin':
        if is_ajax:
            return jsonify({"success": False, "error": "Non autorisé."}), 401
        return redirect(url_for('login'))

    file = request.files.get('signature_image')
    if not file or not file.filename:
        if is_ajax:
            return jsonify({"success": False, "error": "Veuillez sélectionner un fichier image."})
        flash("Veuillez sélectionner un fichier image.", "error")
        return redirect(url_for('doctor_profile'))

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in SIGNATURE_ALLOWED_EXTENSIONS:
        if is_ajax:
            return jsonify({"success": False, "error": "Format invalide. Veuillez importer une image PNG ou JPG."})
        flash("Format invalide. Veuillez importer une image PNG ou JPG.", "error")
        return redirect(url_for('doctor_profile'))

    medecin_id = safe_object_id(session['user_id'])
    signature_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'doctor_signatures', str(medecin_id))
    os.makedirs(signature_dir, exist_ok=True)

    # Remove any previously-saved signature file (possibly a different extension)
    for existing in os.listdir(signature_dir):
        if existing.startswith('signature.'):
            try:
                os.remove(os.path.join(signature_dir, existing))
            except OSError:
                pass

    filename = f"signature.{ext}"
    filepath = os.path.join(signature_dir, filename)
    file.save(filepath)

    db.medecins.update_one(
        {"_id": medecin_id},
        {"$set": {"signature_image_path": filepath, "signature_updated_at": datetime.now()}}
    )
    log_audit_action("Import Signature", "Le médecin a importé/mis à jour sa signature manuscrite")

    if is_ajax:
        return jsonify({"success": True, "image_url": url_for('serve_own_doctor_signature')})

    flash("Signature importée avec succès. Elle sera utilisée automatiquement lors de vos prochaines signatures.", "success")
    return redirect(url_for('doctor_profile'))

@app.route('/doctor/profile/signature')
def serve_own_doctor_signature():
    if 'role' not in session or session['role'] != 'medecin':
        return redirect(url_for('login'))

    medecin = db.medecins.find_one({"_id": safe_object_id(session['user_id'])})
    path = medecin.get('signature_image_path') if medecin else None
    if not path or not os.path.exists(path):
        return "Aucune signature importée.", 404

    return send_file(path)

@app.route('/doctor/enter_result', methods=['POST'])
def enter_result():
    if 'role' not in session or session['role'] != 'medecin':
        return redirect(url_for('login'))
        
    id_reservation = request.form.get('id_reservation')
    observations = request.form.get('observations', '').strip()
    diagnosis = request.form.get('diagnosis', '').strip()
    results = request.form.get('results', '').strip()
    recommendations = request.form.get('recommendations', '').strip()
    conclusion = request.form.get('conclusion', '').strip()
    lab_data = request.form.get('lab_data', '[]')
    
    import json
    try:
        lab_items = json.loads(lab_data)
    except:
        lab_items = []
        
    type_resultat = request.form.get('type_resultat')
    type_signature = request.form.get('type_signature')
    
    reservation = db.reservations.find_one({"_id": ObjectId(id_reservation)})
    patient = db.patients.find_one({"_id": reservation['id_patient']})
    centre = db.centres.find_one({"_id": reservation['id_centre']})
    first_exam = db.examens.find_one({"_id": reservation['examens_ids'][0]})
    
    base_dir, results_dir = create_patient_archive(patient, centre, reservation, first_exam['categorie'])
    images_medicales_dir = get_images_medicales_dir(results_dir, first_exam['categorie'])

    attached_images = []
    if images_medicales_dir and 'medical_images' in request.files:
        files = [f for f in request.files.getlist('medical_images') if f and f.filename]
        existing_count = len([
            f for f in os.listdir(images_medicales_dir)
            if f.startswith('Image_')
        ]) if os.path.isdir(images_medicales_dir) else 0
        for index, file in enumerate(files, start=1):
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'bin'
            filename = f"Image_{existing_count + index:02d}.{ext}"
            filepath = os.path.join(images_medicales_dir, filename)
            file.save(filepath)
            attached_images.append(filepath)

    resultat = {
        "id_reservation": ObjectId(id_reservation),
        "id_medecin": safe_object_id(session['user_id']),
        "date_resultat": datetime.now(),
        "statut_resultat": "Signé par Médecin" if type_resultat == 'immediat' else "Saisi",
        "observations": observations,
        "diagnosis": diagnosis,
        "results": results,
        "recommendations": recommendations,
        "conclusion": conclusion,
        "lab_items": lab_items,
        "images": attached_images
    }
    
    res_result = db.resultats.find_one_and_replace(
        {"id_reservation": ObjectId(id_reservation)},
        resultat,
        upsert=True,
        return_document=True
    )
    resultat_id = res_result['_id'] if res_result else db.resultats.find_one({"id_reservation": ObjectId(id_reservation)})['_id']
    
    # 4. Generate Doctor Cryptographic Digital Signature
    date_sig = datetime.now()
    sig_input = f"{id_reservation}-{session['user_id']}-{observations}-{date_sig.isoformat()}-doctor-secret"
    sig_hash = hashlib.sha256(sig_input.encode('utf-8')).hexdigest()
    
    signature = {
        "id_resultat": resultat_id,
        "type_signature": type_signature,
        "date_signature": date_sig,
        "signature_hash": sig_hash,
        "signataire_nom": f"Dr. {session['user_name']}"
    }
    db.signatures.insert_one(signature)
    
    # 5. Generate Initial Consolidated Report PDF (represented as HTML file in results folder)
    base_dir, results_dir = create_patient_archive(patient, centre, reservation, first_exam['categorie'])
    report_filename = f"rapport_final_{reservation['code_secret']}.html"
    report_filepath = os.path.join(results_dir, report_filename)
    
    exams = list(db.examens.find({"_id": {"$in": reservation['examens_ids']}}))
    medecin_sig = signature  # Just generated above
    medecin_doc = db.medecins.find_one({"_id": safe_object_id(session['user_id'])})
    sig_image_uri = get_doctor_signature_data_uri(medecin_doc) if centre.get('report_show_signature', True) else None
    sig_image_html = f'<img src="{sig_image_uri}" alt="Signature manuscrite" style="max-height:70px; margin-bottom:8px; display:block;">' if sig_image_uri else ''

    hospital_logo_uri = get_hospital_logo_data_uri(centre) if centre.get('report_show_logo', True) else None
    hospital_logo_html = f'<img src="{hospital_logo_uri}" alt="Logo" style="height:36px; margin-bottom:6px;">' if hospital_logo_uri else ''

    with open(report_filepath, 'w', encoding='utf-8') as rf:
        rf.write(f"""
        <html>
        <head>
            <meta charset="utf-8">
            <title>Rapport Medical Final #{reservation['numero_de_rendez_vous']}</title>
            <style>
                body {{ font-family: Arial, sans-serif; padding: 30px; line-height: 1.5; color: #333; }}
                .hdr {{ border-bottom: 2px solid #1B7F3A; padding-bottom: 10px; margin-bottom: 20px; }}
                .sec {{ margin-bottom: 20px; }}
                .box {{ background: #F5F5F5; padding: 15px; border-radius: 4px; border: 1px solid #ddd; margin-bottom: 15px; }}
                .sig-box {{ display: flex; flex-direction: column; gap: 20px; margin-top: 30px; }}
                .sig {{ border: 1px solid #2E9E4D; background: #D9F2D9; padding: 10px; border-radius: 4px; font-size: 11px; }}
                .sig.adm {{ border-color: #2196F3; background: #E8F0FE; }}
                h4 {{ margin-top: 0; color: #1B7F3A; margin-bottom: 5px; }}
                p {{ margin-top: 0; white-space: pre-wrap; }}
                table {{ width: 100%; border-collapse: collapse; margin-bottom: 15px; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #f1f5f9; }}
                .signature-cursive {{ font-family: 'Brush Script MT', cursive; font-size: 24px; color: #0f4c2a; margin: 10px 0; }}
            </style>
        </head>
        <body>
            <div class="hdr">
                {hospital_logo_html}
                <h2>{centre['nom_centre']} - RAPPORT CLINIQUE DEFINITIF</h2>
                <p>Adresse: {centre['adresse']} | Tél: {centre['telephone']}</p>
            </div>
            <div class="sec">
                <strong>Patient:</strong> {patient['prenom']} {patient['nom']} (NI: {patient['numero_national']})<br>
                <strong>Date de visite:</strong> {reservation['date_reservation'].strftime('%d/%m/%Y %H:%M')}<br>
                <strong>Examens:</strong> {', '.join([e['type_examen'] for e in exams])}
            </div>
            
            <div class="box">
                <h4>Observations Médicales</h4>
                <p>{observations}</p>
            </div>
            <div class="box">
                <h4>Diagnostic</h4>
                <p>{diagnosis}</p>
            </div>
            <div class="box">
                <h4>Résultats de l'Examen</h4>
                <p>{results}</p>
            </div>
        """)
        
        if lab_items:
            rf.write("<h4>Tableau des Résultats d'Analyse (Laboratoire)</h4><table><tr><th>Paramètre</th><th>Résultat</th><th>Valeur de référence</th><th>Unité</th><th>Observation</th></tr>")
            for item in lab_items:
                rf.write(f"<tr><td>{item.get('param','')}</td><td>{item.get('val','')}</td><td>{item.get('ref','')}</td><td>{item.get('unit','')}</td><td>{item.get('obs','')}</td></tr>")
            rf.write("</table>")
            
        if attached_images:
            rf.write("<div class='box'><h4>Fichiers d'Imagerie Joints</h4><ul>")
            for img_path in attached_images:
                rf.write(f"<li>{os.path.basename(img_path)}</li>")
            rf.write("</ul></div>")
            
        rf.write(f"""
            <div class="box">
                <h4>Recommandations</h4>
                <p>{recommendations}</p>
            </div>
            <div class="box">
                <h4>Conclusion</h4>
                <p>{conclusion}</p>
            </div>
            
            <div class="sig-box">
                <div class="sig adm" id="admin_sig_placeholder">
                    <strong>VALIDATION ADMINISTRATIVE</strong><br>
                    En attente de co-signature...
                    <!-- ADMIN_SIGNATURE_PLACEHOLDER -->
                </div>
                <div class="sig">
                    <strong>CERTIFICATION MEDICALE</strong><br>
                    {sig_image_html}
                    <div class="signature-cursive">{medecin_sig['signataire_nom']}</div>
                    Date: {medecin_sig['date_signature'].strftime('%d/%m/%Y %H:%M:%S')}<br>
                    Méthode: {medecin_sig['type_signature']}<br>
                    Hash: <code>{medecin_sig['signature_hash']}</code>
                </div>
            </div>
            <p style="font-size:10px; color:#999; text-align:center; margin-top:40px;">
                Ce document a été généré électroniquement et signé cryptographiquement.<br>
                Identifiant du document: {sig_hash}
            </p>
        </body>
        </html>
        """)

    # 6. Handle workflows
    if type_resultat == 'immediat':
        db.reservations.update_one(
            {"_id": ObjectId(id_reservation)},
            {"$set": {"statut": "Signé par Médecin"}}
        )
        log_audit_action("Signature Resultat Medecin", f"Resultat immédiat signe pour #{reservation['numero_de_rendez_vous']}")
        flash('Résultat saisi et signé. En attente de co-signature administrative.', 'success')
    else:
        # Deferred result: status becomes "Saisi"
        db.reservations.update_one(
            {"_id": ObjectId(id_reservation)},
            {"$set": {"statut": "Saisi"}}
        )
        
        # Send delayed result SMS notification (exact text from document)
        sms_text = (
            "Votre examen est en cours d'analyse. Le résultat sera disponible aujourd'hui dans l'après-midi "
            "ou à la date indiquée. Vous recevrez une nouvelle notification dès qu'il sera prêt."
        )
        send_simulated_sms(patient['telephone'], sms_text)
        
        log_audit_action("Signature Resultat Medecin", f"Resultat différé (En cours d'analyse) pour #{reservation['numero_de_rendez_vous']}")
        flash('Résultat différé enregistré. Notification SMS envoyée au patient.', 'warning')
        
    return redirect(url_for('doctor_dashboard'))


# --- ADMINISTRATION WORKFLOWS ---
@app.route('/admin/dashboard')
def admin_dashboard():
    """Legacy route — redirect to the new Identity Verification page."""
    return redirect(url_for('admin_identity'))

# ══════════════════════════════════════════════
# HOSPITAL DASHBOARD — DEDICATED PAGES
# ══════════════════════════════════════════════

@app.route('/admin/identity')
def admin_identity():
    """Page 1: Identity Verification & Entry Validation"""
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    search_query = request.args.get('q', '').strip()
    search_results = []

    if search_query:
        patients = list(db.patients.find({
            "$or": [
                {"numero_national": {"$regex": search_query, "$options": "i"}},
                {"nom": {"$regex": search_query, "$options": "i"}},
                {"prenom": {"$regex": search_query, "$options": "i"}}
            ]
        }))
        patient_ids = [p['_id'] for p in patients]
        search_results = list(db.reservations.find({
            "$or": [
                {"code_secret": {"$regex": search_query, "$options": "i"}},
                {"id_patient": {"$in": patient_ids}}
            ]
        }))
        for res in search_results:
            res['patient'] = db.patients.find_one({"_id": res['id_patient']})
            res['centre'] = db.centres.find_one({"_id": res['id_centre']})
            res['examens'] = list(db.examens.find({"_id": {"$in": res['examens_ids']}}))
            res['paiement'] = db.paiements.find_one({"id_reservation": res['_id']})
            if res['centre'] and len(res.get('examens', [])) > 0:
                specialty_filter = exam_categories_for_examens(res['examens'])
                res['available_doctors'] = list(db.medecins.find({
                    "specialite": {"$in": specialty_filter}
                })) if specialty_filter else []
            else:
                res['available_doctors'] = []

    pending_receipts = list(db.reservations.find({"statut": "En attente de validation"}))
    for res in pending_receipts:
        res['patient'] = db.patients.find_one({"_id": res['id_patient']})
        res['centre'] = db.centres.find_one({"_id": res['id_centre']})
        res['examens'] = list(db.examens.find({"_id": {"$in": res['examens_ids']}}))
        res['paiement'] = db.paiements.find_one({"id_reservation": res['_id']})
        res['cnam'] = db.cnam.find_one({"id_reservation": res['_id']})
        if res['centre'] and len(res.get('examens', [])) > 0:
            specialty_filter = exam_categories_for_examens(res['examens'])
            res['available_doctors'] = list(db.medecins.find({
                "specialite": {"$in": specialty_filter}
            })) if specialty_filter else []
        else:
            res['available_doctors'] = []
    # Count validated today
    from datetime import date
    today_start = datetime.combine(date.today(), datetime.min.time())
    validated_today = db.reservations.count_documents({
        "statut": {"$ne": "En attente de validation"},
        "date_reservation": {"$gte": today_start}
    })

    return render_template(
        'admin_identity.html',
        pending_receipts=pending_receipts,
        search_query=search_query,
        search_results=search_results,
        validated_today=validated_today
    )


@app.route('/admin/preview_report/<reservation_id>')
def admin_preview_report(reservation_id):
    """Lets the administration preview the doctor's report before it has been
    co-signed/validated, using the exact same template (result_print.html)
    the patient will see afterwards in their Dossier Médical — the normal
    /download/result route is locked to 'Terminé' reservations only, and the
    raw static rapport_final_*.html file uses a different, older layout, so
    neither can serve as a faithful "what the patient will see" preview."""
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    res = db.reservations.find_one({"_id": ObjectId(reservation_id)})
    if not res:
        return "Réservation introuvable.", 404

    patient = db.patients.find_one({"_id": res['id_patient']})
    centre = db.centres.find_one({"_id": res['id_centre']})
    exams = list(db.examens.find({"_id": {"$in": res['examens_ids']}}))
    result = db.resultats.find_one({"id_reservation": res['_id']})
    if not result or not exams:
        return "Rapport non disponible.", 404
    medecin = db.medecins.find_one({"_id": result['id_medecin']})

    medecin_signature = db.signatures.find_one({
        "id_resultat": result['_id'],
        "signataire_nom": {"$regex": "^Dr\\."}
    })
    admin_signature = db.signatures.find_one({
        "id_resultat": result['_id'],
        "signataire_nom": {"$regex": "^Admin\\."}
    })
    show_signature = centre.get('report_show_signature', True) if centre else True
    medecin_signature_image = None
    if show_signature:
        medecin_signature_image = get_doctor_signature_data_uri(medecin) if medecin else None
        if not medecin_signature_image and centre and centre.get('default_signature_medecin_id'):
            fallback_medecin = db.medecins.find_one({"_id": centre['default_signature_medecin_id']})
            medecin_signature_image = get_doctor_signature_data_uri(fallback_medecin) if fallback_medecin else None

    hospital_logo = get_hospital_logo_data_uri(centre) if centre and centre.get('report_show_logo', True) else None
    hospital_stamp = get_hospital_stamp_data_uri(centre) if centre and centre.get('report_show_stamp', True) else None
    hospital_director_signature = get_hospital_director_signature_data_uri(centre) if centre and centre.get('report_show_signature', True) else None
    show_header = centre.get('report_show_header', True) if centre else True
    show_footer = centre.get('report_show_footer', True) if centre else True

    first_exam = exams[0]
    images_served = []
    if result.get('images'):
        for img_path in result['images']:
            img_filename = os.path.basename(img_path)
            images_served.append(f"/patient_results/Patient_{patient['numero_national']}/{first_exam['categorie']}/{img_filename}")

    log_audit_action("Consultation Document", f"Aperçu du rapport avant validation #{res['numero_de_rendez_vous']}")

    return render_template(
        'result_print.html',
        reservation=res,
        patient=patient,
        centre=centre,
        exams=exams,
        result=result,
        medecin=medecin,
        medecin_signature=medecin_signature,
        medecin_signature_image=medecin_signature_image,
        admin_signature=admin_signature,
        hospital_logo=hospital_logo,
        hospital_stamp=hospital_stamp,
        hospital_director_signature=hospital_director_signature,
        show_header=show_header,
        show_footer=show_footer,
        attached_images=images_served,
        date_impression=datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
        is_preview=True
    )

@app.route('/admin/results')
def admin_results():
    """Page 2: Results Validation & Administrative Co-signature"""
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    pending_results = list(db.reservations.find({"statut": {"$in": ["Saisi", "Signé par Médecin"]}}))
    for res in pending_results:
        res['patient'] = db.patients.find_one({"_id": res['id_patient']})
        res['centre'] = db.centres.find_one({"_id": res['id_centre']})
        res['examens'] = list(db.examens.find({"_id": {"$in": res['examens_ids']}}))
        res['paiement'] = db.paiements.find_one({"id_reservation": res['_id']})
        res['resultat'] = db.resultats.find_one({"id_reservation": res['_id']})
        if res['resultat']:
            res['medecin'] = db.medecins.find_one({"_id": res['resultat']['id_medecin']})
            res['medecin_signature'] = db.signatures.find_one({
                "id_resultat": res['resultat']['_id'],
                "signataire_nom": {"$regex": "^Dr\\."}
            })
            res['medecin_sig_image'] = get_doctor_signature_data_uri(res['medecin']) if res['medecin_signature'] else None
            image_urls = []
            if res['resultat'].get('images') and res['patient'] and res['examens']:
                categorie = res['examens'][0]['categorie']
                for img_path in res['resultat']['images']:
                    img_filename = os.path.basename(img_path)
                    image_urls.append(f"/patient_results/Patient_{res['patient']['numero_national']}/{categorie}/{img_filename}")
            res['image_urls'] = image_urls
            res['report_preview_url'] = url_for('admin_preview_report', reservation_id=res['_id'])

    # Count signed this month
    month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    signed_this_month = db.reservations.count_documents({
        "statut": "Terminé",
        "date_reservation": {"$gte": month_start}
    })

    return render_template(
        'admin_results.html',
        pending_results=pending_results,
        signed_this_month=signed_this_month,
        cnam_rate=app.config['CNAM_COVERAGE_RATE']
    )


@app.route('/admin/documents')
def admin_documents():
    """Page 3: Documents Archive & Medical Folders"""
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    search_query = request.args.get('q', '').strip()
    search_results = []

    if search_query:
        patients = list(db.patients.find({
            "$or": [
                {"numero_national": {"$regex": search_query, "$options": "i"}},
                {"nom": {"$regex": search_query, "$options": "i"}}
            ]
        }))
        patient_ids = [p['_id'] for p in patients]
        search_results = list(db.reservations.find({
            "statut": "Terminé",
            "$or": [
                {"code_secret": {"$regex": search_query, "$options": "i"}},
                {"id_patient": {"$in": patient_ids}}
            ]
        }))
        for res in search_results:
            res['patient'] = db.patients.find_one({"_id": res['id_patient']})
            res['centre'] = db.centres.find_one({"_id": res['id_centre']})
            res['examens'] = list(db.examens.find({"_id": {"$in": res['examens_ids']}}))
            res['paiement'] = db.paiements.find_one({"id_reservation": res['_id']})

    archived_files = list(db.reservations.find({"statut": "Terminé"}).sort("date_reservation", -1))
    for res in archived_files:
        res['patient'] = db.patients.find_one({"_id": res['id_patient']})
        res['centre'] = db.centres.find_one({"_id": res['id_centre']})
        res['examens'] = list(db.examens.find({"_id": {"$in": res['examens_ids']}}))
        res['paiement'] = db.paiements.find_one({"id_reservation": res['_id']})
        res['resultat'] = db.resultats.find_one({"id_reservation": res['_id']})
        if res['resultat']:
            res['medecin'] = db.medecins.find_one({"_id": res['resultat']['id_medecin']})
            res['medecin_signature'] = db.signatures.find_one({
                "id_resultat": res['resultat']['_id'],
                "signataire_nom": {"$regex": "^Dr\\."}
            })
            res['admin_signature'] = db.signatures.find_one({
                "id_resultat": res['resultat']['_id'],
                "signataire_nom": {"$regex": "^Admin\\."}
            })
            res['medecin_sig_image'] = get_doctor_signature_data_uri(res['medecin']) if res['medecin_signature'] else None
            res['admin_sig_image'] = get_hospital_director_signature_data_uri(res['centre']) if res['admin_signature'] else None
            image_urls = []
            if res['resultat'].get('images') and res['patient'] and res['examens']:
                categorie = res['examens'][0]['categorie']
                for img_path in res['resultat']['images']:
                    img_filename = os.path.basename(img_path)
                    image_urls.append(f"/patient_results/Patient_{res['patient']['numero_national']}/{categorie}/{img_filename}")
            res['image_urls'] = image_urls

    return render_template(
        'admin_documents.html',
        archived_files=archived_files,
        search_query=search_query,
        search_results=search_results
    )


@app.route('/admin/doctors')
def admin_doctors_page():
    """Page 4: Doctors Management"""
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin = db.administrations.find_one({"_id": safe_object_id(session['user_id'])})
    centre_name = admin.get('centre_medical', '') if admin else ''

    medecins = list(db.medecins.find({"centre_medical": centre_name}).sort("nom", 1))
    specialties = set(m.get('specialite', '') for m in medecins if m.get('specialite'))
    specialties_count = len(specialties)

    return render_template(
        'admin_doctors.html',
        medecins=medecins,
        specialties_count=specialties_count,
        exam_categories=get_exam_categories()
    )

# ══════════════════════════════════════════════
# HOSPITAL SETTINGS
# ══════════════════════════════════════════════
HOSPITAL_ASSET_ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

def _get_admin_centre():
    admin = db.administrations.find_one({"_id": safe_object_id(session['user_id'])})
    if not admin:
        return None, None
    centre = db.centres.find_one({"nom_centre": admin.get('centre_medical')})
    return admin, centre

def _save_hospital_asset(centre_id, asset_name, file):
    """Shared upload logic for logo/stamp/director_signature — mirrors the
    doctor-signature upload convention (fixed filename, last-upload-wins)."""
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in HOSPITAL_ASSET_ALLOWED_EXTENSIONS:
        return None, "Format invalide. Veuillez importer une image PNG ou JPG."

    asset_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'hospital_assets', str(centre_id))
    os.makedirs(asset_dir, exist_ok=True)

    for existing in os.listdir(asset_dir):
        if existing.startswith(f"{asset_name}."):
            try:
                os.remove(os.path.join(asset_dir, existing))
            except OSError:
                pass

    filename = f"{asset_name}.{ext}"
    filepath = os.path.join(asset_dir, filename)
    file.save(filepath)
    return filepath, None

def _measure_curved_text(text, font, tracking_ratio=0.22):
    """Return (per-char advance widths, tracking px) for `text` in `font`,
    used to lay glyphs out by their real measured width instead of assuming
    equal angular spacing — this is what prevents long strings from
    overlapping themselves on the curve."""
    probe = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    widths = [max(probe.textlength(ch, font=font), 1) for ch in text]
    tracking = font.size * tracking_ratio
    return widths, tracking

def _fit_curved_font(text, font_path, max_size, min_size, radius, max_span_deg):
    """Pick the largest font size (within [min_size, max_size]) whose curved
    layout of `text` fits inside `max_span_deg` at the given radius, so text
    never overlaps itself regardless of length."""
    for size in range(max_size, min_size - 1, -1):
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        widths, tracking = _measure_curved_text(text, font)
        total_width = sum(widths) + tracking * max(len(text) - 1, 0)
        span_deg = math.degrees(total_width / radius)
        if span_deg <= max_span_deg or size == min_size:
            return font, widths, tracking
    return font, widths, tracking

def _paste_curved_text_measured(canvas, text, widths, tracking, center, radius, font, center_deg, fill, flip=False):
    """Paste `text` glyph-by-glyph along a circular arc, centered on
    `center_deg` (0 = top / 12 o'clock, clockwise), spacing each glyph by its
    real measured width so the curve never overlaps or looks cramped.
    `flip` orients glyphs for the bottom arc, where letters must point
    inward (toward center) to read upright instead of upside-down."""
    if not text:
        return
    total_width = sum(widths) + tracking * max(len(widths) - 1, 0)
    start_deg = center_deg - math.degrees((total_width / 2) / radius)
    cx, cy = center
    cursor = 0.0
    for ch, w in zip(text, widths):
        raw_center = cursor + w / 2
        # Flipped (bottom-arc) glyphs are rotated 180° to read upright, which
        # also mirrors the reading direction — traverse the arc backwards to compensate.
        char_center = (total_width - raw_center) if flip else raw_center
        deg = start_deg + math.degrees(char_center / radius)
        rad = math.radians(deg)
        x = cx + radius * math.sin(rad)
        y = cy - radius * math.cos(rad)
        glyph = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glyph)
        gd.text((32, 32), ch, font=font, fill=fill, anchor='mm')
        rot_deg = (-deg + 180) if flip else -deg
        rotated = glyph.rotate(rot_deg, resample=Image.BICUBIC, expand=True)
        rw, rh = rotated.size
        canvas.paste(rotated, (int(x - rw / 2), int(y - rh / 2)), rotated)
        cursor += w + tracking

def generate_circular_stamp_image(centre, logo_path=None):
    """Build a professional circular hospital stamp (logo centered, hospital
    name curved along the top arc, address/postal/phone/email curved along
    the bottom arc) and return it as a PIL Image (RGBA)."""
    size = 560
    margin = 16
    ring_gap = 64  # generous enough for text to breathe, without an oversized gap
    ink = (16, 60, 140, 255)
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    center = (size // 2, size // 2)
    outer_r = size // 2 - margin
    inner_r = outer_r - ring_gap

    draw.ellipse(
        [center[0] - outer_r, center[1] - outer_r, center[0] + outer_r, center[1] + outer_r],
        outline=ink, width=4
    )
    draw.ellipse(
        [center[0] - inner_r, center[1] - inner_r, center[0] + inner_r, center[1] + inner_r],
        outline=ink, width=3
    )

    font_path = r'C:\Windows\Fonts\arialbd.ttf' if os.path.exists(r'C:\Windows\Fonts\arialbd.ttf') else None

    org_name = (centre.get('stamp_org_name') or centre.get('nom_centre') or '').strip().upper()
    address = (centre.get('stamp_address') or centre.get('adresse') or '').strip()
    postal_code = (centre.get('stamp_postal_code') or '').strip()
    phone = (centre.get('stamp_phone') or centre.get('telephone') or '').strip()
    email = (centre.get('stamp_email') or '').strip()
    bottom_line = '  •  '.join([p for p in [address, postal_code, phone, email] if p]).upper()

    text_radius = (outer_r + inner_r) / 2
    max_font, min_font = 30, 11
    # Text sits on its own arc, leaving a small seam gap on each side of the ring
    # so the top and bottom inscriptions never visually collide at 3 and 9 o'clock.
    max_span_deg = 160

    if org_name:
        font, widths, tracking = _fit_curved_font(org_name, font_path, max_font, min_font, text_radius, max_span_deg)
        _paste_curved_text_measured(img, org_name, widths, tracking, center, text_radius, font, 0, ink)
    if bottom_line:
        font, widths, tracking = _fit_curved_font(bottom_line, font_path, max_font - 6, min_font, text_radius, max_span_deg)
        _paste_curved_text_measured(img, bottom_line, widths, tracking, center, text_radius, font, 180, ink, flip=True)

    if logo_path and os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert('RGBA')
            logo_r = int(inner_r * 0.72)
            logo.thumbnail((logo_r * 2, logo_r * 2), Image.LANCZOS)
            lx = center[0] - logo.width // 2
            ly = center[1] - logo.height // 2
            img.paste(logo, (lx, ly), logo)
        except Exception:
            pass

    return img

def generate_rectangular_stamp_image(centre):
    """Build a professional text-only rectangular hospital stamp (no logo),
    matching the style of a real medical rubber stamp: a bordered box with
    the practitioner/hospital name in bold caps, followed by centered lines
    of specialty, hospital, address and registration details."""
    width = 620
    padding_x = 40
    line_gap_ratio = 1.4
    ink = (10, 10, 10, 255)

    font_path_bold = r'C:\Windows\Fonts\arialbd.ttf' if os.path.exists(r'C:\Windows\Fonts\arialbd.ttf') else None
    font_path_reg = r'C:\Windows\Fonts\arial.ttf' if os.path.exists(r'C:\Windows\Fonts\arial.ttf') else None
    name_font = ImageFont.truetype(font_path_bold, 28) if font_path_bold else ImageFont.load_default()
    line_font = ImageFont.truetype(font_path_reg, 20) if font_path_reg else ImageFont.load_default()

    doctor_name = (centre.get('stamp_doctor_name') or '').strip().upper()
    specialty = (centre.get('stamp_specialty') or '').strip()
    org_name = (centre.get('stamp_org_name') or centre.get('nom_centre') or '').strip().upper()
    address = (centre.get('stamp_address') or centre.get('adresse') or '').strip().upper()
    postal_code = (centre.get('stamp_postal_code') or '').strip()
    phone = (centre.get('stamp_phone') or centre.get('telephone') or '').strip()
    registration = (centre.get('stamp_registration_number') or '').strip()

    lines = []
    if doctor_name:
        lines.append((doctor_name, name_font))
    if specialty:
        lines.append((specialty.upper(), line_font))
    if org_name and org_name != doctor_name:
        lines.append((org_name, line_font))
    if address:
        lines.append((address, line_font))
    contact_line = '  •  '.join([p for p in [postal_code, phone] if p])
    if contact_line:
        lines.append((contact_line, line_font))
    if registration:
        lines.append((registration, line_font))

    if not lines:
        lines = [("CACHET OFFICIEL", name_font)]

    probe = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    line_heights = [font.size * line_gap_ratio for _, font in lines]
    content_height = sum(line_heights)
    height = int(content_height + padding_x * 0.9)

    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([6, 6, width - 6, height - 6], outline=ink, width=3)

    y = (height - content_height) / 2
    for (text, font), line_h in zip(lines, line_heights):
        w = probe.textlength(text, font=font)
        x = (width - w) / 2
        draw.text((x, y + (line_h - font.size) / 2), text, font=font, fill=ink)
        y += line_h

    return img

def _list_backups():
    backups_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
    if not os.path.isdir(backups_dir):
        return []
    entries = [d for d in os.listdir(backups_dir) if d.startswith('backup_') and os.path.isdir(os.path.join(backups_dir, d))]
    entries.sort(reverse=True)
    return entries

@app.route('/admin/settings')
def admin_hospital_settings():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin, centre = _get_admin_centre()
    if not centre:
        flash("Centre médical introuvable.", "error")
        return redirect(url_for('admin_identity'))

    medecins = list(db.medecins.find({"centre_medical": admin.get('centre_medical')}).sort("nom", 1))
    backups = _list_backups()
    last_backup = backups[0] if backups else None

    return render_template(
        'admin_settings.html',
        centre=centre,
        medecins=medecins,
        backups=backups,
        last_backup=last_backup,
        exam_categories=get_exam_categories()
    )

@app.route('/admin/settings/info/update', methods=['POST'])
def admin_settings_update_info():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin, centre = _get_admin_centre()
    if not centre:
        flash("Centre médical introuvable.", "error")
        return redirect(url_for('admin_identity'))

    update_fields = {
        "nom_centre_ar": request.form.get('nom_centre_ar', '').strip(),
        "nom_centre_fr": request.form.get('nom_centre_fr', '').strip(),
        "nom_centre_en": request.form.get('nom_centre_en', '').strip(),
        "description": request.form.get('description', '').strip(),
        "adresse": request.form.get('adresse', '').strip(),
        "telephone": request.form.get('telephone', '').strip(),
        "email": request.form.get('email', '').strip(),
        "website": request.form.get('website', '').strip(),
    }
    if not centre.get('date_creation'):
        update_fields['date_creation'] = datetime.now()

    db.centres.update_one({"_id": centre['_id']}, {"$set": update_fields})
    log_audit_action("Paramètres Hôpital", "Informations de l'hôpital mises à jour")
    flash("Informations de l'hôpital enregistrées avec succès.", "success")
    return redirect(url_for('admin_hospital_settings'))

@app.route('/admin/settings/logo/upload', methods=['POST'])
def admin_settings_upload_logo():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin, centre = _get_admin_centre()
    if not centre:
        flash("Centre médical introuvable.", "error")
        return redirect(url_for('admin_identity'))

    file = request.files.get('logo_image')
    if not file or not file.filename:
        flash("Veuillez sélectionner un fichier image.", "error")
        return redirect(url_for('admin_hospital_settings'))

    filepath, error = _save_hospital_asset(centre['_id'], 'logo', file)
    if error:
        flash(error, "error")
        return redirect(url_for('admin_hospital_settings'))

    db.centres.update_one({"_id": centre['_id']}, {"$set": {"logo_image_path": filepath, "logo_updated_at": datetime.now()}})
    log_audit_action("Paramètres Hôpital", "Logo de l'hôpital mis à jour")
    flash("Logo importé avec succès.", "success")
    return redirect(url_for('admin_hospital_settings'))

@app.route('/admin/settings/logo/remove', methods=['POST'])
def admin_settings_remove_logo():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin, centre = _get_admin_centre()
    if not centre:
        flash("Centre médical introuvable.", "error")
        return redirect(url_for('admin_identity'))

    path = centre.get('logo_image_path')
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass

    db.centres.update_one({"_id": centre['_id']}, {"$set": {"logo_image_path": None, "logo_updated_at": None}})
    log_audit_action("Paramètres Hôpital", "Logo de l'hôpital supprimé")
    flash("Logo supprimé.", "success")
    return redirect(url_for('admin_hospital_settings'))

@app.route('/admin/settings/stamp/upload', methods=['POST'])
def admin_settings_upload_stamp():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin, centre = _get_admin_centre()
    if not centre:
        flash("Centre médical introuvable.", "error")
        return redirect(url_for('admin_identity'))

    file = request.files.get('stamp_image')
    stamp_type = request.form.get('stamp_type', 'rectangulaire')
    if not file or not file.filename:
        flash("Veuillez sélectionner un fichier image.", "error")
        return redirect(url_for('admin_hospital_settings'))

    filepath, error = _save_hospital_asset(centre['_id'], 'stamp', file)
    if error:
        flash(error, "error")
        return redirect(url_for('admin_hospital_settings'))

    db.centres.update_one(
        {"_id": centre['_id']},
        {"$set": {"stamp_image_path": filepath, "stamp_type": stamp_type, "stamp_updated_at": datetime.now()}}
    )
    log_audit_action("Paramètres Hôpital", f"Cachet officiel ({stamp_type}) mis à jour")
    flash("Cachet officiel importé avec succès.", "success")
    return redirect(url_for('admin_hospital_settings'))

@app.route('/admin/settings/stamp/generate', methods=['POST'])
def admin_settings_generate_stamp():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin, centre = _get_admin_centre()
    if not centre:
        flash("Centre médical introuvable.", "error")
        return redirect(url_for('admin_identity'))

    stamp_fields = {
        "stamp_org_name": request.form.get('stamp_org_name', '').strip(),
        "stamp_address": request.form.get('stamp_address', '').strip(),
        "stamp_postal_code": request.form.get('stamp_postal_code', '').strip(),
        "stamp_phone": request.form.get('stamp_phone', '').strip(),
        "stamp_email": request.form.get('stamp_email', '').strip(),
    }
    db.centres.update_one({"_id": centre['_id']}, {"$set": stamp_fields})
    centre.update(stamp_fields)

    asset_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'hospital_assets', str(centre['_id']))
    os.makedirs(asset_dir, exist_ok=True)
    for existing in os.listdir(asset_dir):
        if existing.startswith("stamp."):
            try:
                os.remove(os.path.join(asset_dir, existing))
            except OSError:
                pass

    stamp_image = generate_circular_stamp_image(centre, logo_path=centre.get('logo_image_path'))
    filepath = os.path.join(asset_dir, 'stamp.png')
    stamp_image.save(filepath)

    db.centres.update_one(
        {"_id": centre['_id']},
        {"$set": {"stamp_image_path": filepath, "stamp_type": "circulaire", "stamp_updated_at": datetime.now()}}
    )
    log_audit_action("Paramètres Hôpital", "Cachet officiel circulaire généré automatiquement")
    flash("Cachet officiel circulaire généré et enregistré avec succès. Il remplace tout cachet précédent.", "success")
    return redirect(url_for('admin_hospital_settings'))

@app.route('/admin/settings/stamp/generate_rectangular', methods=['POST'])
def admin_settings_generate_rectangular_stamp():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin, centre = _get_admin_centre()
    if not centre:
        flash("Centre médical introuvable.", "error")
        return redirect(url_for('admin_identity'))

    stamp_fields = {
        "stamp_doctor_name": request.form.get('stamp_doctor_name', '').strip(),
        "stamp_specialty": request.form.get('stamp_specialty', '').strip(),
        "stamp_org_name": request.form.get('stamp_org_name', '').strip(),
        "stamp_address": request.form.get('stamp_address', '').strip(),
        "stamp_postal_code": request.form.get('stamp_postal_code', '').strip(),
        "stamp_phone": request.form.get('stamp_phone', '').strip(),
        "stamp_registration_number": request.form.get('stamp_registration_number', '').strip(),
    }
    db.centres.update_one({"_id": centre['_id']}, {"$set": stamp_fields})
    centre.update(stamp_fields)

    asset_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'hospital_assets', str(centre['_id']))
    os.makedirs(asset_dir, exist_ok=True)
    for existing in os.listdir(asset_dir):
        if existing.startswith("stamp."):
            try:
                os.remove(os.path.join(asset_dir, existing))
            except OSError:
                pass

    stamp_image = generate_rectangular_stamp_image(centre)
    filepath = os.path.join(asset_dir, 'stamp.png')
    stamp_image.save(filepath)

    db.centres.update_one(
        {"_id": centre['_id']},
        {"$set": {"stamp_image_path": filepath, "stamp_type": "rectangulaire", "stamp_updated_at": datetime.now()}}
    )
    log_audit_action("Paramètres Hôpital", "Cachet officiel rectangulaire généré automatiquement")
    flash("Cachet officiel rectangulaire généré et enregistré avec succès. Il remplace tout cachet précédent.", "success")
    return redirect(url_for('admin_hospital_settings'))

@app.route('/admin/settings/director_signature/upload', methods=['POST'])
def admin_settings_upload_director_signature():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin, centre = _get_admin_centre()
    if not centre:
        flash("Centre médical introuvable.", "error")
        return redirect(url_for('admin_identity'))

    if centre.get('director_signature_image_path'):
        flash("Une signature du directeur existe déjà. Supprimez-la d'abord avant d'en importer une nouvelle.", "error")
        return redirect(url_for('admin_hospital_settings'))

    file = request.files.get('director_signature_image')
    if not file or not file.filename:
        flash("Veuillez sélectionner un fichier image.", "error")
        return redirect(url_for('admin_hospital_settings'))

    filepath, error = _save_hospital_asset(centre['_id'], 'director_signature', file)
    if error:
        flash(error, "error")
        return redirect(url_for('admin_hospital_settings'))

    db.centres.update_one(
        {"_id": centre['_id']},
        {"$set": {"director_signature_image_path": filepath, "director_signature_updated_at": datetime.now()}}
    )
    log_audit_action("Paramètres Hôpital", "Signature du directeur importée")
    flash("Signature du directeur importée avec succès. Elle sera utilisée automatiquement lors des validations.", "success")
    return redirect(url_for('admin_hospital_settings'))

@app.route('/admin/settings/director_signature/remove', methods=['POST'])
def admin_settings_remove_director_signature():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin, centre = _get_admin_centre()
    if not centre:
        flash("Centre médical introuvable.", "error")
        return redirect(url_for('admin_identity'))

    path = centre.get('director_signature_image_path')
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass

    db.centres.update_one(
        {"_id": centre['_id']},
        {"$set": {"director_signature_image_path": None, "director_signature_updated_at": None}}
    )
    log_audit_action("Paramètres Hôpital", "Signature du directeur supprimée")
    flash("Signature du directeur supprimée. Vous pouvez maintenant en importer une nouvelle.", "success")
    return redirect(url_for('admin_hospital_settings'))

@app.route('/admin/settings/asset/<asset_name>')
def admin_settings_serve_asset(asset_name):
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin, centre = _get_admin_centre()
    if not centre:
        return "Centre médical introuvable.", 404

    field_map = {
        'logo': 'logo_image_path',
        'stamp': 'stamp_image_path',
        'director_signature': 'director_signature_image_path'
    }
    field = field_map.get(asset_name)
    if not field:
        return "Ressource inconnue.", 404

    path = centre.get(field)
    if not path or not os.path.exists(path):
        return "Fichier introuvable.", 404

    return send_file(path)

@app.route('/admin/settings/branding/update', methods=['POST'])
def admin_settings_update_branding():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin, centre = _get_admin_centre()
    if not centre:
        flash("Centre médical introuvable.", "error")
        return redirect(url_for('admin_identity'))

    update_fields = {
        f"report_show_{key}": (request.form.get(f"report_show_{key}") == 'on')
        for key in ['logo', 'stamp', 'signature', 'qr', 'header', 'footer']
    }
    db.centres.update_one({"_id": centre['_id']}, {"$set": update_fields})
    log_audit_action("Paramètres Hôpital", "Habillage des rapports médicaux mis à jour")
    flash("Préférences d'habillage des rapports enregistrées.", "success")
    return redirect(url_for('admin_hospital_settings'))

@app.route('/admin/settings/default_signature/update', methods=['POST'])
def admin_settings_update_default_signature():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin, centre = _get_admin_centre()
    if not centre:
        flash("Centre médical introuvable.", "error")
        return redirect(url_for('admin_identity'))

    medecin_id = request.form.get('default_signature_medecin_id')
    db.centres.update_one(
        {"_id": centre['_id']},
        {"$set": {"default_signature_medecin_id": safe_object_id(medecin_id) if medecin_id else None}}
    )
    log_audit_action("Paramètres Hôpital", "Signature par défaut mise à jour")
    flash("Signature par défaut mise à jour.", "success")
    return redirect(url_for('admin_hospital_settings'))

@app.route('/admin/doctor/<medecin_id>/signature/upload', methods=['POST'])
def admin_upload_doctor_signature(medecin_id):
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    medecin = db.medecins.find_one({"_id": safe_object_id(medecin_id)})
    if not medecin:
        flash("Médecin introuvable.", "error")
        return redirect(url_for('admin_hospital_settings'))

    file = request.files.get('signature_image')
    if not file or not file.filename:
        flash("Veuillez sélectionner un fichier image.", "error")
        return redirect(url_for('admin_hospital_settings'))

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in SIGNATURE_ALLOWED_EXTENSIONS:
        flash("Format invalide. Veuillez importer une image PNG ou JPG.", "error")
        return redirect(url_for('admin_hospital_settings'))

    signature_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'doctor_signatures', str(medecin['_id']))
    os.makedirs(signature_dir, exist_ok=True)
    for existing in os.listdir(signature_dir):
        if existing.startswith('signature.'):
            try:
                os.remove(os.path.join(signature_dir, existing))
            except OSError:
                pass

    filepath = os.path.join(signature_dir, f"signature.{ext}")
    file.save(filepath)

    db.medecins.update_one(
        {"_id": medecin['_id']},
        {"$set": {"signature_image_path": filepath, "signature_updated_at": datetime.now()}}
    )
    log_audit_action("Paramètres Hôpital", f"Signature du Dr. {medecin['prenom']} {medecin['nom']} importée par l'administration")
    flash(f"Signature du Dr. {medecin['prenom']} {medecin['nom']} importée avec succès.", "success")
    return redirect(url_for('admin_hospital_settings'))

@app.route('/admin/settings/password/update', methods=['POST'])
def admin_settings_update_password():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    admin = db.administrations.find_one({"_id": safe_object_id(session['user_id'])})
    if not admin:
        return redirect(url_for('login'))

    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    if not check_password_hash(admin['password'], current_password):
        flash("Mot de passe actuel incorrect.", "error")
        return redirect(url_for('admin_hospital_settings'))

    if len(new_password) < 8:
        flash("Le nouveau mot de passe doit contenir au moins 8 caractères.", "error")
        return redirect(url_for('admin_hospital_settings'))

    if new_password != confirm_password:
        flash("La confirmation du mot de passe ne correspond pas.", "error")
        return redirect(url_for('admin_hospital_settings'))

    db.administrations.update_one({"_id": admin['_id']}, {"$set": {"password": generate_password_hash(new_password)}})
    log_audit_action("Paramètres Hôpital", "Mot de passe administrateur modifié")
    flash("Mot de passe mis à jour avec succès.", "success")
    return redirect(url_for('admin_hospital_settings'))

@app.route('/admin/settings/backup/create')
def admin_settings_create_backup():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    from bson import json_util
    import json as json_module

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups', f"backup_{timestamp}")
    os.makedirs(backup_dir, exist_ok=True)

    for collection_name in db.list_collection_names():
        docs = list(db[collection_name].find({}))
        file_path = os.path.join(backup_dir, f"{collection_name}.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            json_module.dump(docs, f, default=json_util.default, indent=2)

    log_audit_action("Paramètres Hôpital", f"Sauvegarde créée: backup_{timestamp}")
    flash(f"Sauvegarde créée avec succès : backup_{timestamp}", "success")
    return redirect(url_for('admin_hospital_settings'))

@app.route('/admin/plannings')
def admin_plannings_page():
    """Page 5: Plannings and Groups Management"""
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
        
    admin = db.administrations.find_one({"_id": safe_object_id(session['user_id'])})
    if not admin:
        return redirect(url_for('logout'))
        
    centre_name = admin.get('centre_medical')
    centres = list(db.centres.find({"nom_centre": centre_name}))
    
    selected_centre_id = str(centres[0]['_id']) if centres else None
    
    selected_date = request.args.get('date')
    if not selected_date:
        selected_date = datetime.now().strftime('%Y-%m-%d')
        
    plannings = []
    all_plannings = []
    groupes = []
    if selected_centre_id:
        plannings = list(db.plannings.find({
            "id_centre": ObjectId(selected_centre_id),
            "date": selected_date
        }).sort("start_time", 1))
        # Full list across every date — this is what's actually displayed in the
        # Créneaux Horaires table now, so previously-added slots for other dates
        # are never hidden/lost when a new one is added for a different date.
        all_plannings = list(db.plannings.find({
            "id_centre": ObjectId(selected_centre_id)
        }).sort([("date", 1), ("start_time", 1)]))
        groupes = list(db.groupes.find({
            "id_centre": ObjectId(selected_centre_id),
            "date": selected_date
        }))

    # Full list across every date — same reasoning as Créneaux Horaires: a
    # group created for a date other than the page's current date filter must
    # never appear to have "disappeared" when the admin navigates back here.
    all_groupes = []
    if selected_centre_id:
        all_groupes = list(db.groupes.find({
            "id_centre": ObjectId(selected_centre_id)
        }).sort([("date", 1)]))

    # Map plannings to their labels for the groups view — built from
    # all_plannings (every date), so a group's associated slot always
    # resolves correctly even if it's not on the currently-selected date.
    planning_labels = {str(p['_id']): f"{p['date']} | {p['start_time']} - {p['end_time']}" for p in all_plannings}

    assigned_planning_ids = [str(g.get('id_planning', '')) for g in groupes]
    available_plannings = [p for p in plannings if str(p['_id']) not in assigned_planning_ids]

    # "Créneau Horaire Principal" dropdown for the group-creation form: always
    # sourced live from every planning slot created in Gestion des Plannings
    # (all dates, not just the page's current date filter), auto-updating
    # whenever a slot is added/edited/deleted — excluding slots already
    # assigned to a group anywhere, not just on the selected date.
    all_assigned_planning_ids = []
    available_plannings_for_group = []
    if selected_centre_id:
        all_assigned_planning_ids = [
            str(g.get('id_planning', '')) for g in db.groupes.find({"id_centre": ObjectId(selected_centre_id)})
        ]
        available_plannings_for_group = [
            p for p in all_plannings if str(p['_id']) not in all_assigned_planning_ids
        ]

    return render_template(
        'admin_plannings.html',
        centres=centres,
        selected_centre_id=selected_centre_id,
        selected_date=selected_date,
        plannings=plannings,
        all_plannings=all_plannings,
        available_plannings=available_plannings,
        available_plannings_for_group=available_plannings_for_group,
        groupes=groupes,
        all_groupes=all_groupes,
        planning_labels=planning_labels
    )

@app.route('/admin/plannings/create', methods=['POST'])
def admin_create_planning():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
        
    id_centre = request.form.get('id_centre')
    planning_date = request.form.get('planning_date')
    start_time = request.form.get('start_time')
    end_time = request.form.get('end_time')
    
    db.plannings.insert_one({
        "id_centre": ObjectId(id_centre),
        "date": planning_date,
        "start_time": start_time,
        "end_time": end_time,
        "is_active": True
    })
    log_audit_action("Création Planning", f"Créneau {start_time}-{end_time} le {planning_date} créé pour le centre")
    flash('Créneau horaire ajouté avec succès.', 'success')
    return redirect(url_for('admin_plannings_page', date=planning_date))

@app.route('/admin/plannings/update/<planning_id>', methods=['POST'])
def admin_update_planning(planning_id):
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    planning_date = request.form.get('planning_date')
    start_time = request.form.get('start_time')
    end_time = request.form.get('end_time')

    db.plannings.update_one(
        {"_id": ObjectId(planning_id)},
        {"$set": {"date": planning_date, "start_time": start_time, "end_time": end_time}}
    )
    log_audit_action("Modification Planning", f"Créneau modifié : {start_time}-{end_time} le {planning_date}")
    flash('Créneau horaire modifié avec succès.', 'success')
    return redirect(url_for('admin_plannings_page', date=planning_date))

@app.route('/admin/plannings/delete/<planning_id>', methods=['POST'])
def admin_delete_planning(planning_id):
    if 'role' not in session or session['role'] != 'admin':
        return jsonify({"success": False}), 403
    
    # Also delete associated groups
    db.groupes.delete_many({"id_planning": ObjectId(planning_id)})
    db.plannings.delete_one({"_id": ObjectId(planning_id)})
    
    log_audit_action("Suppression Planning", "Créneau supprimé")
    return jsonify({"success": True})

@app.route('/admin/groups/create', methods=['POST'])
def admin_create_group():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
        
    id_centre = request.form.get('id_centre')
    id_planning = request.form.get('id_planning')
    nom_groupe = request.form.get('nom_groupe')
    max_patients = int(request.form.get('max_patients', 10))

    # The group's date is always taken from the selected planning slot itself
    # (not a separate form field) — the Créneau Horaire Principal dropdown now
    # spans every date, so the slot's own date is the only reliable source.
    planning_doc = db.plannings.find_one({"_id": ObjectId(id_planning)})
    if not planning_doc:
        flash("Créneau horaire introuvable.", "error")
        return redirect(url_for('admin_plannings_page'))
    planning_date = planning_doc['date']

    db.groupes.insert_one({
        "id_centre": ObjectId(id_centre),
        "id_planning": ObjectId(id_planning),
        "date": planning_date,
        "nom_groupe": nom_groupe,
        "max_patients": max_patients
    })
    log_audit_action("Création Groupe", f"Groupe {nom_groupe} le {planning_date} créé pour le centre")
    flash('Groupe ajouté avec succès.', 'success')
    return redirect(url_for('admin_plannings_page', date=planning_date))

@app.route('/admin/groups/delete/<group_id>', methods=['POST'])
def admin_delete_group(group_id):
    if 'role' not in session or session['role'] != 'admin':
        return jsonify({"success": False}), 403
        
    db.groupes.delete_one({"_id": ObjectId(group_id)})
    log_audit_action("Suppression Groupe", "Groupe supprimé")
    return jsonify({"success": True})



@app.route('/admin/audit')
def admin_audit():
    """Page 6: Audit Logs — Doctor & Admin actions only (no patient data)."""
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    # Only show admin and medecin actions — never patient data
    audit_logs = list(db.audit_logs.find(
        {"role": {"$in": ["admin", "medecin"]}}
    ).sort("timestamp", -1).limit(150))

    # Doctor accounts list for the status table
    medecins = list(db.medecins.find({}).sort("nom", 1))
    total_medecins = len(medecins)

    # Doctors created this month
    from datetime import timedelta
    month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    created_this_month = db.medecins.count_documents({
        "date_inscription": {"$gte": month_start}
    })

    # Admin + doctor actions in the last 24 hours
    yesterday = datetime.now() - timedelta(hours=24)
    actions_24h = db.audit_logs.count_documents({
        "role": {"$in": ["admin", "medecin"]},
        "timestamp": {"$gte": yesterday}
    })

    return render_template(
        'admin_audit.html',
        audit_logs=audit_logs,
        medecins=medecins,
        total_medecins=total_medecins,
        created_this_month=created_this_month,
        actions_24h=actions_24h
    )

@app.route('/admin/doctors/create', methods=['POST'])
def admin_create_doctor():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
        
    nom = request.form.get('nom_med', '').strip()
    prenom = request.form.get('prenom_med', '').strip()
    numero_nni = request.form.get('numero_nni', '').strip()
    specialite = request.form.get('specialite', '').strip()
    centre_medical = request.form.get('centre_medical_med', '').strip().upper()
    password = request.form.get('password', '')
    
    existing = db.medecins.find_one({"numero_nni": numero_nni})
    if existing:
        flash('Un médecin avec ce numéro NNI existe déjà.', 'error')
        return redirect(request.referrer or url_for('admin_doctors_page'))
    
    medecin = {
        "nom": nom,
        "prenom": prenom,
        "numero_nni": numero_nni,
        "specialite": specialite,
        "centre_medical": centre_medical,
        "date_inscription": datetime.now(),
        "password": generate_password_hash(password)
    }
    db.medecins.insert_one(medecin)
    log_audit_action("Création Médecin", f"Le compte du Dr. {prenom} {nom} a été créé par l'hôpital")
    flash('Compte médecin créé avec succès !', 'success')
    return redirect(request.referrer or url_for('admin_doctors_page'))

@app.route('/admin/doctors/delete/<doctor_id>', methods=['POST'])
def admin_delete_doctor(doctor_id):
    if 'role' not in session or session['role'] != 'admin':
        return jsonify({"success": False, "message": "Non autorisé"}), 403

    try:
        # Try to resolve the doctor by ObjectId first, then fall back to string _id
        doc = None
        try:
            from bson import ObjectId as BsonObjectId
            doc = db.medecins.find_one({"_id": BsonObjectId(doctor_id)})
        except Exception:
            pass  # doctor_id is not a valid ObjectId — try string _id
        
        if doc is None:
            doc = db.medecins.find_one({"_id": doctor_id})
        
        if not doc:
            return jsonify({"success": False, "message": "Médecin introuvable."}), 404

        db.medecins.delete_one({"_id": doc["_id"]})
        log_audit_action(
            "Suppression Médecin",
            f"Le compte du Dr. {doc.get('prenom', '')} {doc.get('nom', '')} a été supprimé"
        )
        return jsonify({
            "success": True,
            "message": f"Dr. {doc.get('prenom', '')} {doc.get('nom', '')} supprimé avec succès."
        })

    except Exception:
        return jsonify({
            "success": False,
            "message": "Impossible de supprimer ce médecin. Veuillez réessayer."
        }), 500

@app.route('/admin/validate_receipt/<reservation_id>', methods=['POST'])
def admin_validate_receipt(reservation_id):
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
        
    assigned_doctor_id = request.form.get('assigned_doctor')
    if assigned_doctor_id:
        medecin = db.medecins.find_one({"_id": safe_object_id(assigned_doctor_id)})
        if not medecin:
            flash("Médecin introuvable.", "error")
            return redirect(url_for('admin_identity'))

        reservation_check = db.reservations.find_one({"_id": ObjectId(reservation_id)})
        examens_check = list(db.examens.find({"_id": {"$in": reservation_check.get('examens_ids', [])}}))
        required_categories = exam_categories_for_examens(examens_check)
        if required_categories and medecin.get('specialite') not in required_categories:
            flash(
                f"Le Dr. {medecin['prenom']} {medecin['nom']} (spécialité: {medecin.get('specialite', '—')}) "
                f"ne correspond pas à la spécialité requise pour cet examen ({', '.join(required_categories)}). "
                "Veuillez sélectionner un médecin de la spécialité correspondante.",
                "error"
            )
            return redirect(url_for('admin_identity'))

        doctor_name = f"Dr. {medecin['prenom']} {medecin['nom']}" if medecin else "Médecin"
        db.reservations.update_one(
            {"_id": ObjectId(reservation_id)},
            {"$set": {
                "statut": "Validé par Admin / En attente d'examen",
                "id_medecin_assigne": safe_object_id(assigned_doctor_id),
                "nom_medecin_assigne": doctor_name
            }}
        )
        
        reservation = db.reservations.find_one({"_id": ObjectId(reservation_id)})
        patient = db.patients.find_one({"_id": reservation['id_patient']})
        sms_text = f"Votre dossier a été validé. Vous serez pris en charge par le {doctor_name}."
        send_simulated_sms(patient['telephone'], sms_text)
        
        flash(f'Entrée validée et dossier assigné au {doctor_name}.', 'success')
    else:
        db.reservations.update_one(
            {"_id": ObjectId(reservation_id)},
            {"$set": {"statut": "Validé par Admin / En attente d'examen"}}
        )
        flash('Entrée validée avec succès.', 'success')
    # Update payment & CNAM status
    db.paiements.update_one(
        {"id_reservation": ObjectId(reservation_id)},
        {"$set": {"statut_paiement": "Validé"}}
    )
    db.cnam.update_one(
        {"id_reservation": ObjectId(reservation_id)},
        {"$set": {"statut": "Validé"}}
    )
    
    res = db.reservations.find_one({"_id": ObjectId(reservation_id)})
    log_audit_action("Validation Reception", f"Recu de reservation #{res['numero_de_rendez_vous']} valide par l'admin")
    
    flash('Entrée patient validée avec succès. Patient orienté vers le médecin.', 'success')
    return redirect(url_for('admin_identity'))

@app.route('/admin/delete_reservation/<reservation_id>', methods=['POST'])
def admin_delete_reservation(reservation_id):
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
        
    try:
        obj_id = ObjectId(reservation_id)
        # Delete related documents
        db.paiements.delete_one({"id_reservation": obj_id})
        db.cnam.delete_one({"id_reservation": obj_id})
        db.resultats.delete_many({"id_reservation": obj_id})
        
        # Log action before deletion
        res = db.reservations.find_one({"_id": obj_id})
        if res:
            log_audit_action("Suppression", f"Réservation #{res.get('numero_de_rendez_vous', '')} supprimée par l'admin")
            
        # Delete the reservation itself
        db.reservations.delete_one({"_id": obj_id})
        
        flash('Réservation supprimée avec succès.', 'success')
    except Exception:
        flash('Erreur lors de la suppression de la réservation.', 'danger')
        
    return redirect(url_for('admin_identity'))
@app.route('/admin/sign_result/<reservation_id>', methods=['POST'])
def admin_sign_result(reservation_id):
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
        
    type_signature = request.form.get('type_signature')
    
    reservation = db.reservations.find_one({"_id": ObjectId(reservation_id)})
    patient = db.patients.find_one({"_id": reservation['id_patient']})
    centre = db.centres.find_one({"_id": reservation['id_centre']})
    first_exam = db.examens.find_one({"_id": reservation['examens_ids'][0]})
    resultat = db.resultats.find_one({"id_reservation": ObjectId(reservation_id)})
    
    # 1. Update Result Status in DB
    db.resultats.update_one(
        {"_id": resultat['_id']},
        {"$set": {"statut_resultat": "Validé par Administration"}}
    )
    
    # 2. Create Cryptographic Co-signature (Admin Stamping)
    date_sig = datetime.now()
    sig_input = f"{reservation_id}-{session['user_id']}-{resultat.get('observations','')}-{date_sig.isoformat()}-admin-secret"
    sig_hash = hashlib.sha256(sig_input.encode('utf-8')).hexdigest()
    
    signature = {
        "id_resultat": resultat['_id'],
        "type_signature": type_signature,
        "date_signature": date_sig,
        "signature_hash": sig_hash,
        "signataire_nom": f"Admin. {session['user_name']}"
    }
    db.signatures.insert_one(signature)
    
    # 3. Update Reservation to Completed
    db.reservations.update_one(
        {"_id": ObjectId(reservation_id)},
        {"$set": {"statut": "Terminé"}}
    )

    # 3bis. Date de sortie is generated automatically here — the actual
    # completion date of the exam — never entered manually, and never set
    # earlier than final validation.
    db.cnam.update_one(
        {"id_reservation": ObjectId(reservation_id)},
        {"$set": {"date_sortie": date_sig.strftime('%Y-%m-%d')}}
    )

    # 4. Stamp the existing Final Report PDF (HTML file) created by the doctor
    patient_dir = os.path.join(app.config['UPLOAD_FOLDER'], f"Patient_{patient['numero_national']}")
    results_dir = os.path.join(patient_dir, f"Resultats_{first_exam['categorie']}")
    report_filename = f"rapport_final_{reservation['code_secret']}.html"
    report_filepath = os.path.join(results_dir, report_filename)
    
    if os.path.exists(report_filepath):
        with open(report_filepath, 'r', encoding='utf-8') as rf:
            content = rf.read()

        stamp_uri = get_hospital_stamp_data_uri(centre) if centre and centre.get('report_show_stamp', True) else None
        stamp_html = f'<img src="{stamp_uri}" alt="Cachet Officiel" style="max-height:80px; margin-bottom:8px; display:block;">' if stamp_uri else ''

        director_sig_uri = get_hospital_director_signature_data_uri(centre) if centre and centre.get('report_show_signature', True) else None
        director_sig_html = f'<img src="{director_sig_uri}" alt="Signature du Directeur" style="max-height:60px; margin-bottom:8px; display:block;">' if director_sig_uri else ''

        admin_sig_html = f"""<strong>VALIDATION ADMINISTRATIVE</strong><br>
                    <div style="display:flex; gap:16px; align-items:flex-end; flex-wrap:wrap; margin-bottom:6px;">
                        {stamp_html}
                        {director_sig_html}
                    </div>
                    <strong>VISA DE L'ADMINISTRATION:</strong> Admin. {session['user_name']}<br>
                    <strong>Date de signature:</strong> {date_sig.strftime('%d/%m/%Y %H:%M:%S')}<br>
                    <strong>Certificat:</strong> {type_signature}<br>
                    <code style="word-break: break-all;">Hash: {sig_hash}</code>"""
        content = content.replace(
            '<strong>VALIDATION ADMINISTRATIVE</strong><br>\n                    En attente de co-signature...\n                    <!-- ADMIN_SIGNATURE_PLACEHOLDER -->',
            admin_sig_html
        )

        with open(report_filepath, 'w', encoding='utf-8') as wf:
            wf.write(content)
            
    # 5. Trigger Final SMS (exact text from document)
    sms_text = f"Votre résultat final est disponible dans votre espace personnel. Code d'accès: {reservation['code_secret']}."
    send_simulated_sms(patient['telephone'], sms_text)
    
    log_audit_action("Co-signature Administration", f"Visa final et cachet apposes sur le rapport #{reservation['numero_de_rendez_vous']}")
    
    flash('Co-signature appliquée avec succès. Le résultat est maintenant publié et archivé dans le dossier patient.', 'success')
    return redirect(url_for('admin_results'))



@app.route('/api/audit_logs')
def get_audit_logs():
    if 'role' not in session or session['role'] != 'admin':
        return jsonify([])
    logs = list(db.audit_logs.find({}).sort("timestamp", -1).limit(50))
    for log in logs:
        log['_id'] = str(log['_id'])
        log['timestamp'] = log['timestamp'].strftime('%d/%m/%Y %H:%M:%S')
    return jsonify(logs)


# --- PRINT / DOWNLOAD SYSTEM ---
@app.route('/download/receipt/<reservation_id>')
def download_receipt(reservation_id):
    res = db.reservations.find_one({"_id": ObjectId(reservation_id)})
    if not res:
        flash('Réservation introuvable.', 'error')
        return redirect(url_for('index'))
        
    patient = db.patients.find_one({"_id": res['id_patient']})
    centre = db.centres.find_one({"_id": res['id_centre']})
    exams = list(db.examens.find({"_id": {"$in": res['examens_ids']}}))
    paiement = db.paiements.find_one({"id_reservation": res['_id']})

    show_qr = centre.get('report_show_qr', True) if centre else True
    qr_code_data = generate_qr_base64(f"Reçu #{res['numero_de_rendez_vous']} - Code: {res['code_secret']}") if show_qr else None
    hospital_logo = get_hospital_logo_data_uri(centre) if centre and centre.get('report_show_logo', True) else None

    log_audit_action("Consultation Document", f"Consultation du recu de reservation #{res['numero_de_rendez_vous']}")
    return render_template(
        'receipt_print.html',
        reservation=res,
        patient=patient,
        centre=centre,
        exams=exams,
        paiement=paiement,
        mode_paiement=paiement['mode_paiement'],
        cnam_rate=app.config['CNAM_COVERAGE_RATE'],
        qr_code_data=qr_code_data,
        hospital_logo=hospital_logo
    )

@app.route('/download/cnam/<reservation_id>')
def download_cnam(reservation_id):
    res = db.reservations.find_one({"_id": ObjectId(reservation_id)})
    if not res:
        flash('Réservation introuvable.', 'error')
        return redirect(url_for('index'))
        
    patient = db.patients.find_one({"_id": res['id_patient']})
    cnam = db.cnam.find_one({"id_reservation": res['_id']})
    if not cnam or not cnam.get('numero_cnam'):
        flash("Aucun document CNAM disponible pour cette réservation.", "error")
        return redirect(url_for('index'))

    log_audit_action("Consultation Document", f"Consultation des documents CNAM de reservation #{res['numero_de_rendez_vous']}")

    # Render CNI files if uploaded
    cni_html = ""
    if cnam.get('cni_files'):
        cni_html = "<h3>Pièces d'identité CNI Téléchargées:</h3>"
        for index, path in enumerate(cnam['cni_files']):
            # Serve files locally
            filename = os.path.basename(path)
            cni_html += f"""
            <div style="margin-bottom: 15px;">
                <p><strong>Fichier CNI {index + 1} :</strong> {filename}</p>
                <img src="/patient_files/Patient_{patient['numero_national']}/{filename}" style="max-width: 400px; border: 1px solid #ccc; border-radius: 4px;">
            </div>
            """
            
    return f"""
    <html>
    <head><title>Prise en Charge CNAM</title></head>
    <body style="font-family: sans-serif; padding: 40px; line-height: 1.6;">
        <h1 style="color: #2196F3;">Fiche de Prise en Charge CNAM</h1>
        <hr>
        <p><strong>N° Assuré CNAM :</strong> {cnam['numero_cnam']}</p>
        <p><strong>Ayant droit / Bénéficiaire :</strong> {cnam['ayant_droit']}</p>
        <p><strong>Lien de Parenté :</strong> {cnam['relation']}</p>
        <p><strong>Type de Couverture :</strong> {cnam['type_couverture']}</p>
        <p><strong>Date d'Expiration :</strong> {cnam['date_expiration']}</p>
        <p><strong>Statut de validation administrative :</strong> {cnam['statut']}</p>
        <hr>
        {cni_html}
        <hr>
        <button onclick="window.print()" style="padding: 10px; background-color: #2196F3; color: white; border: none; cursor: pointer; font-weight: bold;">Imprimer Fiche</button>
    </body>
    </html>
    """

def _load_fiche_context(reservation_id):
    """Shared loader + access-control check for the CNAM Fiche.
    Returns (reservation, patient, centre, cnam, medecin) or None if access denied/not found."""
    res = db.reservations.find_one({"_id": safe_object_id(reservation_id)})
    if not res:
        return None

    role = session.get('role')
    if role == 'patient':
        if res['id_patient'] != safe_object_id(session['user_id']):
            return None
    elif role == 'medecin':
        if str(res.get('id_medecin_assigne', '')) != str(session['user_id']):
            return None
    elif role == 'admin':
        pass
    else:
        return None

    patient = db.patients.find_one({"_id": res['id_patient']})
    centre = db.centres.find_one({"_id": res['id_centre']})
    cnam = db.cnam.find_one({"id_reservation": res['_id']})
    medecin = db.medecins.find_one({"_id": safe_object_id(res.get('id_medecin_assigne'))}) if res.get('id_medecin_assigne') else None
    return res, patient, centre, cnam, medecin

@app.route('/fiche_cnam/<reservation_id>')
def fiche_cnam(reservation_id):
    if 'role' not in session:
        return redirect(url_for('login'))

    ctx = _load_fiche_context(reservation_id)
    if not ctx:
        flash("Fiche CNAM introuvable ou accès non autorisé.", "error")
        return redirect(url_for('index'))
    res, patient, centre, cnam, medecin = ctx

    if not cnam:
        flash("Aucune fiche CNAM n'existe pour cette réservation.", "error")
        return redirect(url_for('index'))

    age = None
    if patient and patient.get('date_naissance'):
        try:
            dob = patient['date_naissance']
            if isinstance(dob, str):
                dob = datetime.strptime(dob, '%Y-%m-%d')
            today = datetime.now()
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        except Exception:
            age = None

    show_qr = centre.get('report_show_qr', True) if centre else True
    verification_url = url_for('fiche_cnam', reservation_id=reservation_id, _external=True)
    qr_code_data = generate_qr_base64(verification_url) if show_qr else None
    hospital_logo = get_hospital_logo_data_uri(centre) if centre and centre.get('report_show_logo', True) else None

    cnam_membership_status = None
    if cnam.get('numero_cnam'):
        cnam_membership_status = "Active"
        exp = cnam.get('date_expiration')
        if exp:
            try:
                exp_date = exp if isinstance(exp, datetime) else datetime.strptime(exp, '%Y-%m-%d')
                if exp_date < datetime.now():
                    cnam_membership_status = "Expirée"
            except Exception:
                pass

    log_audit_action("Consultation Fiche CNAM", f"Consultation de la fiche CNAM de reservation #{res['numero_de_rendez_vous']}")

    return render_template(
        'fiche_cnam_print.html',
        reservation=res,
        patient=patient,
        centre=centre,
        cnam=cnam,
        medecin=medecin,
        age=age,
        qr_code_data=qr_code_data,
        hospital_logo=hospital_logo,
        cnam_rate=app.config['CNAM_COVERAGE_RATE'],
        cnam_membership_status=cnam_membership_status,
        date_impression=datetime.now().strftime('%d/%m/%Y %H:%M')
    )

@app.route('/medecin/fiche_cnam/<reservation_id>/update', methods=['POST'])
def update_fiche_cnam_medecin(reservation_id):
    if 'role' not in session or session['role'] != 'medecin':
        return redirect(url_for('login'))

    ctx = _load_fiche_context(reservation_id)
    if not ctx:
        flash("Fiche CNAM introuvable ou accès non autorisé.", "error")
        return redirect(url_for('index'))
    res, patient, centre, cnam, medecin = ctx

    if not cnam:
        flash("Aucune fiche CNAM n'existe pour cette réservation.", "error")
        return redirect(url_for('index'))

    if cnam.get('valide_administration'):
        flash("Cette fiche a été finalisée par l'administration et ne peut plus être modifiée.", "error")
        return redirect(url_for('fiche_cnam', reservation_id=reservation_id))

    medicaments = request.form.getlist('medicament[]')
    quantites = request.form.getlist('quantite[]')
    durees = request.form.getlist('duree[]')
    notes_med = request.form.getlist('notes_medicament[]')
    prescriptions = [
        {"medicament": m, "quantite": q, "duree": d, "notes": n}
        for m, q, d, n in zip(medicaments, quantites, durees, notes_med)
        if m.strip()
    ]

    analyse_types = request.form.getlist('analyse_type[]')
    analyse_prestations = request.form.getlist('analyse_prestation[]')
    analyse_notes = request.form.getlist('analyse_notes[]')
    analyses = [
        {"type": t, "prestation": p, "notes": n}
        for t, p, n in zip(analyse_types, analyse_prestations, analyse_notes)
        if p.strip()
    ]

    observations_medecin = request.form.get('observations_medecin', '').strip()

    update_fields = {
        "prescriptions": prescriptions,
        "analyses": analyses,
        "observations_medecin": observations_medecin,
        "medecin_updated_at": datetime.now(),
        "id_medecin_editeur": safe_object_id(session['user_id']),
        "updated_at": datetime.now()
    }

    # Auto-populate the Administrative Section from the reservation/payment
    # record already on file, so the admin never has to re-type figures the
    # system already knows — only fills fields the admin hasn't already set.
    paiement = db.paiements.find_one({"id_reservation": res['_id']})
    visit_date = res.get('date_reservation')
    if cnam.get('date_entree') is None and visit_date:
        update_fields['date_entree'] = visit_date.strftime('%Y-%m-%d')
    # Date de sortie is intentionally NOT set here — it is generated only when
    # the file is validated/finalized by the administration (see admin_sign_result).
    if cnam.get('montant_facture') is None and paiement and paiement.get('montant_total') is not None:
        update_fields['montant_facture'] = paiement['montant_total']
    if cnam.get('montant_rembourse_cnam') is None and paiement and paiement.get('cnam_amount') is not None:
        update_fields['montant_rembourse_cnam'] = paiement['cnam_amount']

    db.cnam.update_one(
        {"id_reservation": res['_id']},
        {"$set": update_fields}
    )
    log_audit_action("Mise à jour Fiche CNAM", f"Le médecin a mis à jour la fiche CNAM de la réservation #{res['numero_de_rendez_vous']}")
    flash("Fiche CNAM mise à jour avec succès.", "success")
    return redirect(url_for('fiche_cnam', reservation_id=reservation_id))

@app.route('/admin/fiche_cnam/<reservation_id>/update', methods=['POST'])
def update_fiche_cnam_admin(reservation_id):
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))

    ctx = _load_fiche_context(reservation_id)
    if not ctx:
        flash("Fiche CNAM introuvable.", "error")
        return redirect(url_for('index'))
    res, patient, centre, cnam, medecin = ctx

    if not cnam:
        flash("Aucune fiche CNAM n'existe pour cette réservation.", "error")
        return redirect(url_for('index'))

    # Date d'entrée/sortie and the invoiced/reimbursed amounts are populated
    # automatically (from the reservation/payment record on doctor-save, and
    # date_sortie on final validation) — the administration no longer fills
    # or edits them here, only notes_admin remains a manual field.
    update_fields = {
        "notes_admin": request.form.get('notes_admin', '').strip(),
        "admin_updated_at": datetime.now(),
        "id_admin_validateur": safe_object_id(session['user_id']),
        "updated_at": datetime.now()
    }
    if cnam.get('numero_cnam'):
        update_fields["date_debut_cnam"] = request.form.get('date_debut_cnam') or None

    if request.form.get('finalize') == 'on':
        update_fields["valide_administration"] = True
        log_audit_action("Finalisation Fiche CNAM", f"L'administration a finalisé la fiche CNAM de la réservation #{res['numero_de_rendez_vous']}")
        flash("Fiche CNAM finalisée avec succès.", "success")
    else:
        log_audit_action("Mise à jour Fiche CNAM", f"L'administration a mis à jour la fiche CNAM de la réservation #{res['numero_de_rendez_vous']}")
        flash("Fiche CNAM mise à jour avec succès.", "success")

    db.cnam.update_one({"id_reservation": res['_id']}, {"$set": update_fields})
    return redirect(url_for('fiche_cnam', reservation_id=reservation_id))

@app.route('/api/fiche_cnam/<reservation_id>/status')
def fiche_cnam_status(reservation_id):
    if 'role' not in session:
        return jsonify({"error": "unauthorized"}), 401

    ctx = _load_fiche_context(reservation_id)
    if not ctx:
        return jsonify({"error": "not_found"}), 404
    res, patient, centre, cnam, medecin = ctx

    if not cnam:
        return jsonify({"error": "not_found"}), 404

    updated_at = cnam.get('updated_at')
    return jsonify({"updated_at": updated_at.isoformat() if updated_at else None})

# Secure file routing to display uploaded patient CNI card files
@app.route('/patient_files/Patient_<national_id>/<filename>')
def serve_patient_cni(national_id, filename):
    patient_dir = os.path.join(app.config['UPLOAD_FOLDER'], f"Patient_{national_id}")
    # Search recursively for the filename in the patient directory subfolders
    for root, dirs, files in os.walk(patient_dir):
        if filename in files:
            return send_from_directory(root, filename)
    return "Fichier introuvable", 404

# Serve medical images and final PDF files
@app.route('/patient_results/Patient_<national_id>/<category>/<filename>')
def serve_patient_results(national_id, category, filename):
    patient_dir = os.path.join(app.config['UPLOAD_FOLDER'], f"Patient_{national_id}")
    for root, dirs, files in os.walk(patient_dir):
        if filename in files and f"Resultats_{category}" in root:
            return send_from_directory(root, filename)
    return "Fichier introuvable", 404

@app.route('/download/result/<reservation_id>')
def download_result(reservation_id):
    res = db.reservations.find_one({"_id": ObjectId(reservation_id)})
    if not res or res['statut'] != 'Terminé':
        flash('Résultat non disponible.', 'error')
        return redirect(url_for('index'))
        
    patient = db.patients.find_one({"_id": res['id_patient']})
    centre = db.centres.find_one({"_id": res['id_centre']})
    exams = list(db.examens.find({"_id": {"$in": res['examens_ids']}}))
    result = db.resultats.find_one({"id_reservation": res['_id']})
    medecin = db.medecins.find_one({"_id": result['id_medecin']})
    
    medecin_signature = db.signatures.find_one({
        "id_resultat": result['_id'],
        "signataire_nom": {"$regex": "^Dr\\."}
    })
    admin_signature = db.signatures.find_one({
        "id_resultat": result['_id'],
        "signataire_nom": {"$regex": "^Admin\\."}
    })
    show_signature = centre.get('report_show_signature', True) if centre else True
    medecin_signature_image = None
    if show_signature:
        medecin_signature_image = get_doctor_signature_data_uri(medecin) if medecin else None
        if not medecin_signature_image and centre and centre.get('default_signature_medecin_id'):
            fallback_medecin = db.medecins.find_one({"_id": centre['default_signature_medecin_id']})
            medecin_signature_image = get_doctor_signature_data_uri(fallback_medecin) if fallback_medecin else None

    hospital_logo = get_hospital_logo_data_uri(centre) if centre and centre.get('report_show_logo', True) else None
    hospital_stamp = get_hospital_stamp_data_uri(centre) if centre and centre.get('report_show_stamp', True) else None
    hospital_director_signature = get_hospital_director_signature_data_uri(centre) if centre and centre.get('report_show_signature', True) else None
    show_header = centre.get('report_show_header', True) if centre else True
    show_footer = centre.get('report_show_footer', True) if centre else True

    log_audit_action("Consultation Document", f"Consultation du rapport medical final #{res['numero_de_rendez_vous']}")
    
    # Check if there are attached medical images to serve separately
    images_served = []
    first_exam = exams[0]
    if result.get('images'):
        for img_path in result['images']:
            img_filename = os.path.basename(img_path)
            images_served.append(f"/patient_results/Patient_{patient['numero_national']}/{first_exam['categorie']}/{img_filename}")
            
    return render_template(
        'result_print.html', 
        reservation=res, 
        patient=patient, 
        centre=centre, 
        exams=exams, 
        result=result,
        medecin=medecin,
        medecin_signature=medecin_signature,
        medecin_signature_image=medecin_signature_image,
        admin_signature=admin_signature,
        hospital_logo=hospital_logo,
        hospital_stamp=hospital_stamp,
        hospital_director_signature=hospital_director_signature,
        show_header=show_header,
        show_footer=show_footer,
        attached_images=images_served,
        date_impression=datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    )


# ── API: Patient Documents (for 📂 folder panel) ──────────────────────────
@app.route('/api/patient/documents')
def api_patient_documents():
    if 'role' not in session or session['role'] != 'patient':
        return jsonify([])

    patient = db.patients.find_one({"_id": ObjectId(session['user_id'])})
    if not patient:
        return jsonify([])

    reservations = list(db.reservations.find(
        {"id_patient": patient['_id']}
    ).sort("date_reservation", -1))

    result = []
    for res in reservations:
        centre = db.centres.find_one({"_id": res['id_centre']}) or {}
        exams  = list(db.examens.find({"_id": {"$in": res.get('examens_ids', [])}}))
        cnam_doc = db.cnam.find_one({"id_reservation": res['_id']})
        resultat = db.resultats.find_one({"id_reservation": res['_id']})

        exam_names = ", ".join(e.get('type_examen', '') for e in exams) or "—"
        res_id_str = str(res['_id'])

        entry = {
            "id":        res_id_str,
            "date":      res['date_reservation'].strftime('%d/%m/%Y %H:%M'),
            "centre":    centre.get('nom_centre', '—'),
            "examens":   exam_names,
            "statut":    res.get('statut', '—'),
            "doctor":    res.get('nom_medecin_assigne', 'Non assigné'),
            "receipt_url": f"/download/receipt/{res_id_str}",
            "fiche_cnam_url": f"/fiche_cnam/{res_id_str}" if cnam_doc else None,
            "result_url":  f"/download/result/{res_id_str}" if res.get('statut') == 'Terminé' else None,
            "images":      []
        }

        # Attached medical images
        if resultat and resultat.get('images'):
            first_exam = exams[0] if exams else {}
            categorie  = first_exam.get('categorie', 'General')
            for img_path in resultat['images']:
                img_filename = os.path.basename(img_path)
                entry["images"].append(
                    f"/patient_results/Patient_{patient['numero_national']}/{categorie}/{img_filename}"
                )

        result.append(entry)

    return jsonify(result)


# ── API: SMS Logs ──────────────────────────────────────────────────────────
@app.route('/api/sms_logs', methods=['GET'])
def api_sms_logs():
    query = {}
    if session.get('role') == 'patient':
        patient = db.patients.find_one({"_id": ObjectId(session.get('user_id'))})
        if patient and patient.get('telephone'):
            query = {"phone": patient['telephone']}
            
    logs = list(db.sms_logs.find(query).sort("_id", -1).limit(20))
    return jsonify([{
        "id":      str(l['_id']),
        "time":    l.get('time', ''),
        "phone":   l.get('phone', '—'),
        "message": l.get('message', '')
    } for l in logs])

@app.route('/api/sms_logs/<sms_id>', methods=['DELETE'])
def delete_sms_log(sms_id):
    result = db.sms_logs.delete_one({"_id": ObjectId(sms_id)})
    if result.deleted_count > 0:
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Not found"}), 404

@app.route('/api/sms_logs', methods=['DELETE'])
def delete_all_sms_logs():
    db.sms_logs.delete_many({})
    return jsonify({"status": "success"})


if __name__ == '__main__':
    app.run(debug=True, port=5000)
