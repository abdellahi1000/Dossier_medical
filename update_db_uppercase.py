from pymongo import MongoClient
client = MongoClient('mongodb://localhost:27017/')
db = client['medical_dossier_db']

for centre in db.centres.find({}):
    if 'nom_centre' in centre and isinstance(centre['nom_centre'], str):
        db.centres.update_one({'_id': centre['_id']}, {'$set': {'nom_centre': centre['nom_centre'].upper()}})

for admin in db.administrations.find({}):
    if 'centre_medical' in admin and isinstance(admin['centre_medical'], str):
        db.administrations.update_one({'_id': admin['_id']}, {'$set': {'centre_medical': admin['centre_medical'].upper()}})

for medecin in db.medecins.find({}):
    if 'centre_medical' in medecin and isinstance(medecin['centre_medical'], str):
        db.medecins.update_one({'_id': medecin['_id']}, {'$set': {'centre_medical': medecin['centre_medical'].upper()}})

print('Updated existing DB entries to uppercase on correct DB.')
