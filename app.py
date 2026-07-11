from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from pymongo import MongoClient
from bson.objectid import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os
import random
import string
import hashlib
import re
from PIL import Image, ImageDraw

# Initialize Flask
app = Flask(__name__)
app.config.from_object('config')

# Ensure Upload Folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# MongoDB Connection
client = MongoClient(app.config['MONGO_URI'])
db = client[app.config['DATABASE_NAME']]

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
        d.text((50, 90), f"Centre: Hospitalier / Clinique", fill='#FFFFFF')
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

    # 3. Seed Doctors
    if db.medecins.count_documents({}) == 0:
        medecins = [
            {
                "_id": "med_ahmed",
                "nom": "Ahmed",
                "prenom": "Mohamed",
                "specialite": "Analyse Sanguine",
                "password": generate_password_hash("password")
            },
            {
                "_id": "med_mariam",
                "nom": "Mariam",
                "prenom": "Kane",
                "specialite": "Radiologie",
                "password": generate_password_hash("password")
            }
        ]
        for m in medecins:
            db.medecins.replace_one({"_id": m["_id"]}, m, upsert=True)
        print("Doctors seeded.")

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
        
        for c in centres:
            plannings = [
                {"id_centre": c["_id"], "start_time": "08:00", "end_time": "09:00", "is_active": True},
                {"id_centre": c["_id"], "start_time": "09:00", "end_time": "10:00", "is_active": True},
                {"id_centre": c["_id"], "start_time": "10:30", "end_time": "11:30", "is_active": True},
                {"id_centre": c["_id"], "start_time": "14:00", "end_time": "15:00", "is_active": True}
            ]
            res = db.plannings.insert_many(plannings)
            
            # Create corresponding groups
            groupes = [
                {"id_centre": c["_id"], "id_planning": res.inserted_ids[0], "nom_groupe": "J1", "max_patients": 10},
                {"id_centre": c["_id"], "id_planning": res.inserted_ids[1], "nom_groupe": "J2", "max_patients": 15},
                {"id_centre": c["_id"], "id_planning": res.inserted_ids[2], "nom_groupe": "J3", "max_patients": 10},
                {"id_centre": c["_id"], "id_planning": res.inserted_ids[3], "nom_groupe": "J4", "max_patients": 15}
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
        
        weak_passwords = ['12345678', '123456789', 'password', '11111111', 'qwerty', 'abc12345']
        if len(password) < 8 or password.lower() in weak_passwords:
            flash('Your password is too weak. Please choose a stronger password with at least 8 characters.', 'error')
            return redirect(url_for('login'))
        
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
            log_audit_action("Connexion", "Administrateur connecté avec succès")
            flash('Connexion Hôpital réussie !', 'success')
            return redirect(url_for('admin_dashboard'))
                
        log_audit_action("Echec Connexion", f"Tentative infructueuse, ID: {username}")
        flash('Identifiants incorrects.', 'error')
        return redirect(url_for('login'))
        
    return render_template('login.html')

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
        
    patient = db.patients.find_one({"_id": ObjectId(session['user_id'])})
    reservations = list(db.reservations.find({"id_patient": ObjectId(session['user_id'])}).sort("date_reservation", -1))
    
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
        
    patient = db.patients.find_one({"_id": ObjectId(session['user_id'])})
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
        
    patient = db.patients.find_one({"_id": ObjectId(session['user_id'])})
    
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
            "id_patient": ObjectId(session['user_id']),
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
                
        cnam_amount = round(montant_total * 0.9, 2) if mode_paiement == 'CNAM' else 0
        patient_amount = round(montant_total * 0.1, 2) if mode_paiement == 'CNAM' else montant_total
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
            numero_cnam = patient.get('nni')
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
        
    medecin = db.medecins.find_one({"_id": ObjectId(session['user_id'])})
    specialty = medecin['specialite']
    
    exams_of_specialty = list(db.examens.find({"categorie": specialty}))
    exam_ids = [ex['_id'] for ex in exams_of_specialty]
    search_code = request.args.get('code_secret', '').strip()
    query = {
        "id_medecin_assigne": ObjectId(session['user_id']),
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
    
    attached_images = []
    if 'medical_images' in request.files:
        files = request.files.getlist('medical_images')
        for file in files:
            if file and file.filename:
                import werkzeug.utils
                filename = werkzeug.utils.secure_filename(file.filename)
                filepath = os.path.join(results_dir, filename)
                file.save(filepath)
                attached_images.append(filepath)
                
    resultat = {
        "id_reservation": ObjectId(id_reservation),
        "id_medecin": session['user_id'],
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
                .sig-box {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 30px; }}
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
                <div class="sig">
                    <strong>CERTIFICATION MEDICALE</strong><br>
                    <div class="signature-cursive">{medecin_sig['signataire_nom']}</div>
                    Date: {medecin_sig['date_signature'].strftime('%d/%m/%Y %H:%M:%S')}<br>
                    Méthode: {medecin_sig['type_signature']}<br>
                    Hash: <code>{medecin_sig['signature_hash']}</code>
                </div>
                <div class="sig adm" id="admin_sig_placeholder">
                    <strong>VALIDATION ADMINISTRATIVE</strong><br>
                    En attente de co-signature...
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
            f"Votre examen est en cours d'analyse. Le résultat sera disponible aujourd'hui dans l'après-midi "
            f"ou à la date indiquée. Vous recevrez une nouvelle notification dès qu'il sera prêt."
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
                cat = res['examens'][0].get('categorie', '')
                specialty_filter = ["Analyse Sanguine"] if cat == "Analyse Sanguine" else ["Radiologie / Cardiologie", "Cardiologique", "Radiologique"]
                res['available_doctors'] = list(db.medecins.find({
                    "specialite": {"$in": specialty_filter}
                }))
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
            cat = res['examens'][0].get('categorie', '')
            specialty_filter = ["Analyse Sanguine"] if cat == "Analyse Sanguine" else ["Radiologie / Cardiologie", "Cardiologique", "Radiologique"]
            res['available_doctors'] = list(db.medecins.find({
                "specialite": {"$in": specialty_filter}
            }))
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
        res['resultat'] = db.resultats.find_one({"id_reservation": res['_id']})
        if res['resultat']:
            res['medecin'] = db.medecins.find_one({"_id": res['resultat']['id_medecin']})
            res['medecin_signature'] = db.signatures.find_one({
                "id_resultat": res['resultat']['_id'],
                "signataire_nom": {"$regex": "^Dr\\."}  
            })

    # Count signed this month
    from datetime import date
    month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    signed_this_month = db.reservations.count_documents({
        "statut": "Terminé",
        "date_reservation": {"$gte": month_start}
    })

    return render_template(
        'admin_results.html',
        pending_results=pending_results,
        signed_this_month=signed_this_month
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

    medecins = list(db.medecins.find({}).sort("nom", 1))
    specialties = set(m.get('specialite', '') for m in medecins if m.get('specialite'))
    specialties_count = len(specialties)

    return render_template(
        'admin_doctors.html',
        medecins=medecins,
        specialties_count=specialties_count
    )

@app.route('/admin/plannings')
def admin_plannings_page():
    """Page 5: Plannings and Groups Management"""
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
        
    admin = db.administrations.find_one({"_id": ObjectId(session['user_id'])})
    if not admin:
        return redirect(url_for('logout'))
        
    centre_name = admin.get('centre_medical')
    centres = list(db.centres.find({"nom_centre": centre_name}))
    
    selected_centre_id = str(centres[0]['_id']) if centres else None
    
    selected_date = request.args.get('date')
    if not selected_date:
        selected_date = datetime.now().strftime('%Y-%m-%d')
        
    plannings = []
    groupes = []
    if selected_centre_id:
        plannings = list(db.plannings.find({
            "id_centre": ObjectId(selected_centre_id),
            "date": selected_date
        }).sort("start_time", 1))
        groupes = list(db.groupes.find({
            "id_centre": ObjectId(selected_centre_id),
            "date": selected_date
        }))
        
    # Map plannings to their labels for the groups view
    planning_labels = {str(p['_id']): f"{p['start_time']} - {p['end_time']}" for p in plannings}
    
    assigned_planning_ids = [str(g.get('id_planning', '')) for g in groupes]
    available_plannings = [p for p in plannings if str(p['_id']) not in assigned_planning_ids]
    
    return render_template(
        'admin_plannings.html',
        centres=centres,
        selected_centre_id=selected_centre_id,
        selected_date=selected_date,
        plannings=plannings,
        available_plannings=available_plannings,
        groupes=groupes,
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

@app.route('/admin/plannings/delete/<planning_id>', methods=['POST'])
def admin_delete_planning(planning_id):
    if 'role' not in session or session['role'] != 'admin':
        return jsonify({"success": False}), 403
    
    # Also delete associated groups
    db.groupes.delete_many({"id_planning": ObjectId(planning_id)})
    db.plannings.delete_one({"_id": ObjectId(planning_id)})
    
    log_audit_action("Suppression Planning", f"Créneau supprimé")
    return jsonify({"success": True})

@app.route('/admin/groups/create', methods=['POST'])
def admin_create_group():
    if 'role' not in session or session['role'] != 'admin':
        return redirect(url_for('login'))
        
    id_centre = request.form.get('id_centre')
    id_planning = request.form.get('id_planning')
    planning_date = request.form.get('planning_date')
    nom_groupe = request.form.get('nom_groupe')
    max_patients = int(request.form.get('max_patients', 10))
    
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
    log_audit_action("Suppression Groupe", f"Groupe supprimé")
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

    except Exception as e:
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
        medecin = db.medecins.find_one({"_id": ObjectId(assigned_doctor_id)})
        doctor_name = f"Dr. {medecin['prenom']} {medecin['nom']}" if medecin else "Médecin"
        db.reservations.update_one(
            {"_id": ObjectId(reservation_id)},
            {"$set": {
                "statut": "Validé par Admin / En attente d'examen",
                "id_medecin_assigne": ObjectId(assigned_doctor_id),
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
    except Exception as e:
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
    
    # 4. Stamp the existing Final Report PDF (HTML file) created by the doctor
    patient_dir = os.path.join(app.config['UPLOAD_FOLDER'], f"Patient_{patient['numero_national']}")
    results_dir = os.path.join(patient_dir, f"Resultats_{first_exam['categorie']}")
    report_filename = f"rapport_final_{reservation['code_secret']}.html"
    report_filepath = os.path.join(results_dir, report_filename)
    
    if os.path.exists(report_filepath):
        with open(report_filepath, 'r', encoding='utf-8') as rf:
            content = rf.read()
            
        admin_sig_html = f"""
                <div class="sig adm">
                    <strong>VISA DE L'ADMINISTRATION:</strong> Admin. {session['user_name']}<br>
                    <strong>Date de signature:</strong> {date_sig.strftime('%d/%m/%Y %H:%M:%S')}<br>
                    <strong>Certificat:</strong> {type_signature}<br>
                    <code style="word-break: break-all;">Hash: {sig_hash}</code>
                </div>
                <!-- ADMIN_SIGNATURE_PLACEHOLDER -->
        """
        content = content.replace("<!-- ADMIN_SIGNATURE_PLACEHOLDER -->", admin_sig_html)
        
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
    
    log_audit_action("Consultation Document", f"Consultation du recu de reservation #{res['numero_de_rendez_vous']}")
    return render_template(
        'receipt_print.html', 
        reservation=res, 
        patient=patient, 
        centre=centre, 
        exams=exams, 
        mode_paiement=paiement['mode_paiement']
    )

@app.route('/download/cnam/<reservation_id>')
def download_cnam(reservation_id):
    res = db.reservations.find_one({"_id": ObjectId(reservation_id)})
    if not res:
        flash('Réservation introuvable.', 'error')
        return redirect(url_for('index'))
        
    patient = db.patients.find_one({"_id": res['id_patient']})
    cnam = db.cnam.find_one({"id_reservation": res['_id']})
    
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
        admin_signature=admin_signature,
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
        paiement = db.paiements.find_one({"id_reservation": res['_id']}) or {}
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
            "cnam_url":    f"/download/cnam/{res_id_str}" if paiement.get('mode_paiement') == 'CNAM' else None,
            "cnam_num":    cnam_doc.get('numero_cnam', '') if cnam_doc else '',
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
