- Analyse et Conception

Premierment : Palette de couleurs

| **Couleur**      | **Code CSS** | **Utilisation**                                  |
| ---------------- | ------------ | ------------------------------------------------ |
| Vert principal   | #1B7F3A      | Boutons principaux, menu de navigation, en-têtes |
| Vert secondaire  | #2E9E4D      | Cartes, icônes, éléments interactifs             |
| Vert clair       | #D9F2D9      | Arrière-plan des sections et zones d'information |
| Jaune            | #F4C542      | Notifications, alertes légères et indicateurs    |
| Rouge            | #D62828      | Messages d'erreur, suppression et annulation     |
| Bleu information | #2196F3      | Téléchargement, informations et liens            |
| Blanc            | #FFFFFF      | Arrière-plan principal et formulaires            |
| Gris clair       | #F5F5F5      | Cartes secondaires et zones de contenu           |
| Gris foncé       | #333333      | Titres et textes importants                      |

Deuxiement: Use case,Workflow et UML  
**1\. MCD (Modèle Conceptuel de Données)**

**Entité : Patient**

- id_patient
- nom
- prenom
- numero_national
- telephone
- date_inscription

**Entité : Centre_Medical**

- id_centre
- nom_centre
- adresse
- telephone

**Entité : Examen**

- id_examen
- type_examen
- categorie
- prix

Exemples :(choix multiples)

- Analyse sanguine
- VIH
- FLH
- Radiologie

**Entité : Reservation**

- id_reservation
- date_reservation
- statut
- montant_total
- code_secret
- numero_appel
- lettre

**Entité : Paiement**

- id_paiement
- date_paiement
- montant
- mode_paiement
- statut_paiement

**Entité : CNAM**

- id_cnam
- numero_cnam
- type_couverture

**Entité : Recu**

- id_recu
- date_generation
- Numero_group
- Numero*de* Le_rendez-vous
- Code_secret

**Entité : Medecin**

- id_medecin
- nom
- prenom
- specialite

**Entité : Administration**

- id_admin
- nom
- prenom

**Entité : Resultat**

- id_resultat
- date_resultat
- statut_resultat
- commentaire

**Entité : Signature_Numerique**

- id_signature
- date_signature
- type_signature

**Relations**

Patient (1,N) ----- Effectue ----- (1,1) Reservation

Reservation (1,1) ----- Concerne ----- (1,1) Centre_Medical

Reservation (1,N) ----- Contient ----- (1,N) Examen

Reservation (1,1) ----- Possede ----- (1,1) Paiement

Reservation (0,1) ----- Utilise ----- (0,1) CNAM

Reservation (1,1) ----- Genere ----- (1,1) Recu

Medecin (1,N) ----- Produit ----- (1,1) Resultat

Resultat (1,1) ----- Associe ----- (1,1) Reservation

Resultat (1,1) ----- Possede ----- (1,2) Signature_Numerique

Administration (1,N) ----- Valide ----- (1,N) Resultat

**MLD (Version corrigée)**

**PATIENT  
(  
id_patient PK,  
nom,  
prenom,  
numero_national,  
telephone,  
date_inscription  
)**

**CENTRE_MEDICAL  
(  
id_centre PK,  
nom_centre,  
adresse,  
telephone  
)**

**EXAMEN  
(  
id_examen PK,  
type_examen,  
categorie,  
prix  
)**

**RESERVATION  
(  
id_reservation PK,  
id_patient FK,  
id_centre FK,  
date_reservation,  
montant_total,  
code_secret,  
numero_appel,  
lettre,  
statut  
)**

**RESERVATION_EXAMEN  
(  
id_reservation FK,  
id_examen FK  
)**

**PAIEMENT  
(  
id_paiement PK,  
id_reservation FK,  
montant,  
mode_paiement,  
date_paiement,  
statut_paiement  
)**

**CNAM  
(  
id_cnam PK,  
id_reservation FK,  
numero_cnam,  
type_couverture  
)**

**RECU  
(  
id_recu PK,  
id_reservation FK,  
date_generation,  
numero_group,  
numero_de_rendez_vous,  
code_secret  
)**

**MEDECIN  
(  
id_medecin PK,  
nom,  
prenom,  
specialite  
)**

**ADMINISTRATION  
(  
id_admin PK,  
nom,  
prenom  
)**

**RESULTAT  
(  
id_resultat PK,  
id_reservation FK,  
id_medecin FK,  
date_resultat,  
statut_resultat,  
commentaire  
)**

**SIGNATURE_NUMERIQUE  
(  
id_signature PK,  
id_resultat FK,  
type_signature,  
date_signature  
)  
)**

**3\. Use Case Diagram**

**Acteur : Patient**

Patient  
|  
|--- S'inscrire  
|--- Se connecter  
|--- Choisir un centre médical  
|--- Choisir un type d'examen  
|--- Sélectionner plusieurs examens  
|--- Consulter le montant total  
|--- Choisir CNAM ou paiement normal  
|--- Effectuer le paiement  
|--- Recevoir le reçu par SMS  
|--- Consulter les notifications  
|--- Télécharger le dossier médical  
|--- Télécharger le reçu  
|--- Télécharger les documents CNAM  
|--- Télécharger le résultat final

**Acteur : Médecin**

Médecin  
|  
|--- Se connecter  
|--- Consulter les patients programmés  
|--- Vérifier le reçu du patient  
|--- Réaliser l'examen  
|--- Saisir le résultat  
|--- Déposer le résultat numérique  
|--- Signer numériquement le résultat  
|--- Envoyer le résultat à l'administration

**Acteur : Administration**

Administration  
|  
|--- Se connecter  
|--- Rechercher un patient  
|--- Vérifier numéro national  
|--- Vérifier code secret  
|--- Imprimer le reçu  
|--- Valider les informations  
|--- Recevoir les résultats du médecin  
|--- Signer numériquement les résultats  
|--- Envoyer les résultats au patient  
|--- Envoyer SMS de notification  
|--- Gérer les dossiers numériques

**4.Workflow Réservation et Paiement (Version corrigée)**

Patient  
↓  
Saisie des informations personnelles  
(Nom, Prénom, Numéro national, Téléphone)  
↓  
Choix du centre médical  
↓  
Choix du type d'examen  
↓  
Sélection d'un ou plusieurs examens  
↓  
Calcul automatique du montant total  
↓  
Choix du mode de prise en charge

\[CNAM\]  
ou  
\[Paiement normal\]

↓  
Confirmation de la réservation  
↓  
Paiement  
↓  
Création de la réservation  
↓  
Génération automatique :

- Code secret
- Numéro du groupe
- Numéro de rendez-vous
- Numéro d'appel

↓  
Création du reçu numérique  
↓  
Envoi automatique d'un SMS contenant :

- Centre médical
- Date du rendez-vous
- Code secret
- Numéro du groupe
- Numéro d'appel

↓  
Patient se présente à l'administration  
↓  
Fin du processus de réservation

Médecin  
↓  
Réalisation de l'examen  
↓  
Résultat disponible

SI résultat immédiat  
↓  
Signature médecin  
↓  
Administration  
↓  
Signature administration  
↓  
Notification SMS  
↓  
Publication du résultat

SINON  
↓  
Transmission à l'administration  
↓  
SMS : résultat disponible plus tard  
↓  
Résultat final prêt  
↓  
Notification SMS  
↓  
Publication du résultat

**6.Workflow Envoi des Résultats et Gestion Documentaire (Version corrigée)**

Le patient se présente à l'administration  
↓  
Vérification :

- Numéro national
- Code secret

↓  
Impression ou récupération du reçu  
↓  
Patient orienté vers le médecin  
↓  
Le médecin récupère le reçu  
↓  
Réalisation de l'examen  
↓  
Résultat disponible ?

━━━━━━━━━━━━━━━━━━━━  
CAS 1 : Résultat immédiat  
━━━━━━━━━━━━━━━━━━━━

Médecin saisit le résultat  
↓  
Génération du fichier PDF du résultat  
↓  
Signature numérique du médecin  
↓  
Transmission à l'administration  
↓  
Signature numérique de l'administration  
↓  
Archivage numérique  
↓  
Création automatique du dossier patient

Contenu :

- Dossier médical principal
- Documents CNAM
- Reçu numérique
- Résultat final PDF signé

↓  
Notification SMS  
↓  
Patient accède à son espace  
↓  
Téléchargement des documents  
↓  
Fin

━━━━━━━━━━━━━━━━━━━━  
CAS 2 : Résultat différé  
━━━━━━━━━━━━━━━━━━━━

Médecin transmet le dossier à l'administration  
↓  
Administration envoie un SMS :

"Votre résultat sera disponible ultérieurement."

↓  
Analyse en cours  
↓  
Résultat final disponible  
↓  
Génération du fichier PDF du résultat  
↓  
Signature numérique du médecin  
↓  
Signature numérique de l'administration  
↓  
Archivage numérique  
↓  
Création automatique du dossier patient

Contenu :

- Dossier médical principal
- Documents CNAM
- Reçu numérique
- Résultat final PDF signé

↓  
SMS de disponibilité du résultat  
↓  
Patient se connecte à la plateforme  
↓  
Consultation et téléchargement du résultat  
↓  
Fin