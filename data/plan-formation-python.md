# Parcours de formation Python — apprendre à programmer pour créer

**Programme de tutorat progressif, fondé sur des projets**  
**Référentiel :** objectifs des formations *Apprenez à programmer en Python — niveaux 1 et 2* de l'IFAGE, enrichis de pratiques modernes de développement.  
**Organisation :** par modules et compétences acquises, **sans calendrier ni date limite**.  
**Durées indicatives :** une leçon dure 15 à 35 minutes, cours compris ; un mini-projet 45 à 90 minutes, en une ou plusieurs séances. Ce sont des repères : on avance par maîtrise, pas par heures.  
**Langage enseigné : Python uniquement.** JavaScript et C++ restent des apprentissages autonomes, séparés de nos séances.

> **Notre ambition** : ne pas simplement « connaître la syntaxe Python », mais savoir analyser un problème, concevoir une solution, programmer, comprendre du code existant, déboguer, tester et livrer de petites applications utiles. Les compétences acquises doivent être aussi transférables que possible à d'autres langages.

---

## 1. Philosophie et fonctionnement de la formation

### Répartition des rôles

| Support | Son rôle | Ce qu'il ne remplace pas |
|---|---|---|
| **Notre tutorat IA** | Faire le cours de chaque leçon, faire pratiquer avec une aide progressive, vérifier la compréhension et valider sur des problèmes nouveaux. | Le travail actif de l'apprenant : écrire, tester, se tromper, expliquer. |
| **Éditeur + terminal + Git** | Travailler dans un environnement de développement réel, conserver et faire évoluer le code. | La réflexion préalable à l'écriture du code. |
| **Projets personnels** | Combiner les notions, faire des choix, produire des preuves de compétence. | Les exercices ciblés nécessaires pour consolider certaines bases. |

### Les règles de notre tutorat

1. **Le cours d'abord.** Chaque leçon s'ouvre par un cours condensé — la notion, à quoi elle sert, des exemples avec leur sortie, ce qu'il faut retenir, l'objectif du jour —, même si je connais déjà la notion. Ce que je sais déjà raccourcit le cours (version express) et la pratique, sans les supprimer.
2. **Comprendre avant d'automatiser.** Je dois pouvoir expliquer le principe avec mes mots, pas seulement recopier une solution.
3. **Coder moi-même.** Le tuteur présente le problème, donne des indices graduels et ne livre une solution complète qu'après une vraie tentative ou sur demande explicite.
4. **Progression de l'aide :** question → indice conceptuel → sous-problème → pseudo-code → extrait ciblé → correction commentée.
5. **Alterner les activités :** lire du code, prévoir son résultat, écrire, déboguer, améliorer et tester.
6. **Réviser en contexte.** Les notions précédentes réapparaissent dans les projets suivants, sans recommencer systématiquement un cours entier.
7. **Ne pas confondre assistance IA et compétence.** Certaines validations ont lieu sans génération de code par l'IA ; la documentation officielle reste autorisée.
8. **Avancer par maîtrise, pas par heures.** Un module peut être raccourci si la compétence est démontrée, ou approfondi si nécessaire.
9. **Comparer occasionnellement les concepts entre langages**, mais tous nos exercices, corrections et projets restent en Python. Les particularités JS/C++ ne deviennent pas des objectifs du tutorat.
10. **Cliquer plutôt qu'écrire.** Tout ce qui se choisit se clique : QCM quand une seule réponse est juste, boutons pour oui ou non et pour la suite. J'écris dans la discussion pour expliquer avec mes mots ou poser une question ; le code s'écrit dans l'éditeur.

### Critères pour valider un module

Un module est considéré comme acquis lorsque je peux, sur **un exercice nouveau** :

- [ ] expliquer les principaux concepts sans réciter une définition ;
- [ ] choisir une approche et justifier les structures utilisées ;
- [ ] écrire le cœur du programme sans solution générée ;
- [ ] comprendre les messages d'erreur et corriger des bugs courants ;
- [ ] tester le comportement normal et au moins un cas limite ;
- [ ] relire mon code, repérer une amélioration et l'expliquer.

Un échec n'est pas un retour à zéro : le tuteur identifie précisément la notion fragile et propose un exercice adapté.

---

## 2. Vue d'ensemble du parcours

| Étape | Module | Compétence centrale | Réalisation principale |
|---|---|---|---|
| 0 | **Les bases de Python** | Lire, écrire et lancer ses premiers programmes | Le nombre mystère |
| 1 | **Penser comme un programmeur** | Analyser, décomposer, exécuter, déboguer | Simulateur de jeu simple |
| 2 | **Algorithmes et structures de données** | Choisir et manipuler les données | Jeu d'exploration textuel |
| 3 | **Fonctions et conception** | Structurer une logique réutilisable | Moteur de quiz pédagogique |
| 4 | **Fichiers, erreurs et formats** | Conserver et exploiter des données | Fabrique de questions/fiches |
| 5 | **Modules, tests et qualité** | Maintenir et faire évoluer du code | Refactorisation d'un projet |
| 6 | **Programmation orientée objet** | Modéliser les comportements d'une application | Mini-RPG pédagogique |
| 7 | **Python expressif et avancé** | Manipuler proprement des traitements | Générateur de défis |
| 8 | **Données externes, API et bases SQL** | Connecter une application au monde extérieur | Bibliothèque d'activités pédagogiques |
| 9 | **Analyse et visualisation** | Explorer, transformer et représenter des données | Tableau de bord d'apprentissage fictif |
| 10 | **Interfaces et applications** | Passer du script à un outil utilisable | Application desktop ou API éducative |
| 11 | **Réseau et concurrence** | Comprendre échanges et exécution simultanée | Jeu en réseau très simple ou outil d'automatisation |
| 12 | **Distribution et interopérabilité** | Livrer, documenter et intégrer un logiciel | Version distribuable d'un projet |
| 13 | **Projet final de portfolio** | Concevoir et réaliser de bout en bout | Application originale au choix |

**Organisation générale :** le module 0 fait un premier tour complet de la syntaxe ; les modules 1 à 6 constituent le socle et approfondissent ; les modules 7 à 12 élargissent les capacités ; le module 13 réunit le tout. Certains thèmes du niveau 1 IFAGE (XML, Tkinter, sockets) arrivent volontairement plus tard, quand ils peuvent servir un projet réel.

---

# PARTIE A — CONSTRUIRE DES FONDATIONS SOLIDES

## Module 0 — Les bases de Python

**Question centrale :** comment lire, écrire et lancer ses premiers programmes Python ?

**Principe :** un premier tour complet de la syntaxe, une notion par leçon de 15 à 25 minutes, le cours d'abord, puis la pratique dans l'éditeur. Les modules suivants approfondissent chaque notion dans des problèmes plus riches.

| # | Leçon | Ce qu'elle apprend |
|---|---|---|
| 1 | Premiers pas | `print()`, variables et réaffectation, lancer un script avec `python3` |
| 2 | Types et comparaisons | `int`, `float`, `str`, `bool`, opérateurs, comparaisons, conversions |
| 3 | Conditions | `if` / `elif` / `else`, `and` / `or` / `not`, indentation des blocs |
| 4 | Boucles `for` et `range` | répéter, bornes et pas de `range`, accumulateur |
| 5 | Listes | indices, y compris négatifs ; modification ; `IndexError` |
| 6 | Opérations sur les listes | `append`, `insert`, `remove`, `pop`, `in`, `len`, tranches, tri |
| 7 | `input()` et `while` | saisie et conversion, boucle conditionnelle, redemander une saisie invalide |
| 8 | Opérations sur les chaînes | f-strings, méthodes courantes, tranches, chaîne non modifiable |
| 9 | Fonctions | `def`, paramètres, `return`, différence avec `print` |
| 10 | Tuples, dictionnaires et ensembles | déballage, clés et valeurs, `items()`, doublons |
| 11 | Compréhensions de listes | transformer, filtrer, rester lisible |
| 12 | Classes et objets | `class`, `__init__`, attributs, méthodes, `self` |
| 13 | Héritage | classe enfant, redéfinition, `super()` |
| 14 | Modules | `import`, `from … import`, bibliothèque standard, son propre module |
| 15 | Erreurs et exceptions | lire un message d'erreur, `try` / `except`, `ValueError` |
| 16 | Mini-projet : le nombre mystère | un jeu complet qui réunit le module |

**Pièges à surveiller dès ce module :** `print` ou `return`, indices et bornes, chaîne ou nombre, indentation, mutation d'une liste, boucle infinie.

**Mini-projet — Le nombre mystère :** le programme tire un nombre au hasard ; le joueur propose, le programme répond « plus grand » ou « plus petit » et compte les essais. Une saisie invalide ne fait pas planter le jeu, et l'on peut rejouer. Extensions : niveaux de difficulté, meilleur score.

**Validation :** lire, prédire et modifier un petit script en expliquant son fonctionnement, puis réussir seul le mini-projet. Les notions encore fragiles sont reprises au début du module qui les approfondit.

---

## Module 1 — Penser comme un programmeur et maîtriser son environnement

**Question centrale :** comment passer d'un problème exprimé en français à un programme fiable ?

**Compétences :**

- Reformuler un besoin en **entrées, traitements et sorties** ; le découper en étapes.
- Distinguer une variable, une expression, une instruction et un état.
- Utiliser conditions, boucles, fonctions simples et affichage pour construire un programme.
- Lire une trace d'exécution, reconnaître les erreurs de syntaxe et les erreurs de logique.
- Utiliser Python dans un terminal, VS Code, l'interpréteur interactif et le débogueur ; comprendre ce qu'est l'environnement d'exécution.
- Découvrir les tests simples avec `assert`, puis la logique « attendu / obtenu ».

**Exercices types :** prévoir le résultat d'un script ; corriger une boucle qui ne termine pas ; expliquer pourquoi un score n'évolue pas ; transformer des règles de jeu en pseudo-code.

**Mini-projet — Simulateur de duel :** un personnage et un adversaire ont des points de vie ; chaque tour, le joueur choisit une action ; le programme calcule les conséquences et annonce le vainqueur. Extension : équilibrer les règles et ajouter un mode de difficulté.

**Validation :** transformer des règles inédites en algorithme, programmer une version jouable puis corriger un bug introduit par le tuteur.

**Transfert conceptuel :** conditions, boucles, état et débogage sont des idées communes à JavaScript et C++. Nous pouvons les mentionner brièvement, sans exercice dans ces langages.

---

## Module 2 — Algorithmes, collections et résolution de problèmes

**Question centrale :** comment représenter un problème et choisir la bonne structure de données ?

**Compétences :**

- Maîtriser les types de base et les chaînes (`str`, `int`, `float`, `bool`, `None`).
- Utiliser listes, tuples, ensembles (`set`) et dictionnaires ; distinguer données mutables et immuables.
- Parcourir, rechercher, trier, compter, filtrer et regrouper des données.
- Savoir utiliser `enumerate`, `zip`, `sorted`, `min`, `max`, `sum`, `any`, `all`.
- Comprendre le fonctionnement des index, tranches et copies ; éviter les erreurs de mutation et d'aliasing.
- Introduire les idées de coût d'un algorithme, de complexité et de choix raisonnable de structure, sans formalisme excessif.

**Exercices types :** inventaire d'objets ; détection de doublons ; fréquence des mots ; recherche du meilleur score ; exploration d'une grille ; simulation de lancer de dés.

**Mini-projet — Jeu d'exploration textuel :** représenter une carte par une grille, les objets par des dictionnaires, un inventaire par une collection, et programmer les déplacements ainsi que les interactions.

**Validation :** choisir seul entre liste, tuple, dictionnaire et ensemble pour un problème nouveau, en expliquant le choix.

**Transfert conceptuel :** collections, parcours et complexité se retrouvent ailleurs ; leurs noms, leur sémantique et leur gestion mémoire diffèrent selon les langages.

---

## Module 3 — Fonctions, abstraction et conception d'un programme

**Question centrale :** comment écrire du code que l'on peut comprendre, réutiliser et faire évoluer ?

**Compétences :**

- Définir des fonctions avec `def`, arguments, paramètres, valeurs par défaut et `return`.
- Distinguer **calculer une valeur** et **produire un effet** (affichage, modification, sauvegarde).
- Comprendre la portée des variables et éviter les variables globales inutiles.
- Construire une logique par petites fonctions testables : validation, calcul, sélection, affichage.
- Découvrir annotations de type, docstrings et contrats simples (préconditions, résultat attendu).
- Introduire la récursivité sur un exemple concret, sans en faire la solution systématique.
- Transformer un long script en programme lisible ; apprendre la notion de séparation des responsabilités.

**Projet — Moteur de quiz pédagogique :**

1. Charger provisoirement des questions codées en dur.
2. Présenter une question, recevoir et vérifier une réponse.
3. Calculer un score et proposer un retour pertinent.
4. Tirer des questions selon des règles configurables.
5. Permettre plusieurs modes : révision, défi chronométré, séries d'erreurs.

**Validation :** ajouter un nouveau mode au quiz sans recopier les mêmes traitements à plusieurs endroits.

**Transfert conceptuel :** interface d'une fonction, modularité, séparation du calcul et de l'affichage : applicables en JS et C++.

---

## Module 4 — Fichiers, exceptions et formats d'échange

**Question centrale :** comment un programme exploite-t-il des données qui existent avant son lancement et persistent après ?

**Compétences :**

- Lire, créer, modifier et écrire des fichiers avec `pathlib` et `with` ; comprendre chemins et encodages UTF-8.
- Manipuler des textes, fichiers CSV, JSON et, au besoin, XML (`xml.etree.ElementTree`).
- Utiliser `datetime` pour les dates, heures et durées.
- Rechercher des motifs dans des textes avec `re` (expressions régulières), sans les employer lorsqu'une solution plus simple convient.
- Distinguer erreurs de syntaxe, exceptions et comportements incorrects ; gérer `try`, `except`, `else`, `finally`, `raise`.
- Valider les données entrantes et éviter la perte accidentelle de fichiers.
- Comprendre pourquoi des formats comme JSON ou CSV facilitent les échanges entre applications.

**Projet — Fabrique d'activités pédagogiques :** partir d'un dossier de questions en CSV/JSON, vérifier les contenus, sélectionner des questions par thème ou difficulté et générer des séries d'exercices au format texte ou HTML simple. Ajouter un rapport des lignes mal formées.

**Validation :** traiter un fichier inconnu mais conforme à une spécification, signaler proprement les données invalides et produire un résultat exploitable.

**Transfert conceptuel :** lecture/écriture, encodage, validation, sérialisation et formats JSON/XML dépassent Python.

---

## Module 5 — Modules, outils de développement, tests et maintenance

**Question centrale :** comment passer d'un script qui fonctionne à un projet que l'on peut maintenir ?

**Compétences :**

- Séparer le programme en modules et paquets ; organiser les imports et un point d'entrée `main()`.
- Comprendre les environnements virtuels (`venv`), `pip`, les dépendances et l'intérêt de `pyproject.toml`.
- Utiliser Git pour sauvegarder les étapes et expliquer les changements.
- Écrire des tests unitaires avec `pytest` (ou `unittest`) ; utiliser des cas normaux, limites et invalides.
- Maîtriser le débogueur : point d'arrêt, inspection des variables, pile d'appels.
- Lire une base de code existante : entrée du programme, données, dépendances, tests et circulation des appels.
- Améliorer des noms, simplifier des fonctions, réduire les duplications ; introduire un formateur et un vérificateur de code (ex. Ruff), sans dépendre de l'outil pour comprendre les erreurs.

**Projet — Sauver le projet d'un développeur fictif :** recevoir une application de quiz volontairement mal organisée et partiellement boguée. La comprendre, corriger les erreurs, extraire des modules, documenter et ajouter des tests avant toute nouvelle fonction.

**Validation :** prendre en charge une modification demandée dans un projet inconnu sans casser les comportements existants.

**Transfert conceptuel :** contrôle de version, tests, modules, débogage, dette technique et refactorisation sont très transférables.

---

## Module 6 — Programmation orientée objet : concevoir un petit jeu

**Question centrale :** quand une classe aide-t-elle vraiment à organiser un programme ?

**Compétences :**

- Distinguer classe, instance, attribut, méthode et constructeur `__init__`.
- Comprendre `self`, état d'un objet et règles d'encapsulation.
- Savoir quand préférer une simple fonction, un dictionnaire, une `dataclass` ou une classe.
- Utiliser composition, héritage et polymorphisme ; comprendre leurs avantages et leurs limites.
- Découvrir `@property`, méthodes de classe/statiques selon le besoin.
- Comprendre quelques méthodes spéciales (`__str__`, `__repr__`, `__eq__` et, au besoin, surcharge d'opérateurs).
- Lire, tester et faire évoluer du code orienté objet existant.

**Projet — Mini-RPG pédagogique :** personnages, objets, compétences, quêtes et défis de logique. Les classes représentent les entités réellement utiles. Une énigme réussie ouvre une nouvelle salle ou débloque une compétence. Le jeu reste d'abord jouable dans le terminal : **pas d'interface graphique prématurée**.

**Validation :** étendre un jeu existant en ajoutant un type d'objet ou une règle sans modifier toutes les classes. Expliquer pourquoi la composition peut parfois être préférable à l'héritage.

**Transfert conceptuel :** classes, interfaces, composition, héritage et polymorphisme, avec des différences importantes de langage — particulièrement en C++.

### Jalon A — Autonomie sur les fondamentaux

À l'issue des modules 1 à 6, je dois pouvoir :

- [ ] créer une application Python en ligne de commande depuis un dossier vide ;
- [ ] utiliser fonctions, collections, fichiers et classes quand elles sont pertinentes ;
- [ ] lire, comprendre et modifier un projet existant de plusieurs modules ;
- [ ] écrire des tests simples et corriger des erreurs ;
- [ ] documenter son installation et son utilisation ;
- [ ] expliquer le fonctionnement du code essentiel, sans réponse générée.

**Livrable portfolio possible :** une version soignée du *moteur de quiz pédagogique* ou du *mini-RPG*, avec README, captures/animation si utile, tests et historique Git.

---

# PARTIE B — ÉLARGIR LES OUTILS DU DÉVELOPPEUR PYTHON

## Module 7 — Python idiomatique et programmation avancée

**Question centrale :** comment exploiter les outils expressifs de Python sans rendre le code obscur ?

**Compétences :**

- Utiliser compréhensions de listes, ensembles et dictionnaires en sachant quand une boucle classique est plus claire.
- Comprendre `lambda`, `map`, `filter` et `functools.reduce`, sans les utiliser par principe.
- Écrire des fonctions imbriquées, comprendre les fermetures (*closures*) et les notions de fonction de premier ordre.
- Comprendre itérables, itérateurs, `yield` et générateurs ; traiter des flux progressivement.
- Découvrir les décorateurs et apprendre à **lire** ceux utilisés dans des bibliothèques courantes.
- Comprendre la récursivité et sa pertinence pour des structures arborescentes ou des problèmes de recherche.
- Connaître les bases de l'annotation de types et lire des signatures d'API.

**Projet — Générateur de défis logiques :** créer un système qui produit des énigmes variées (suites, calculs, combinaisons, règles), filtre celles qui respectent certains critères et les distribue sous forme de série. Introduire un générateur pour produire les défis à la demande.

**Validation :** expliquer une expression Python compacte, la réécrire de façon lisible et choisir la forme appropriée à une situation nouvelle.

**Transfert conceptuel :** itération, fonctions comme valeurs, récursivité et traitement différé se retrouvent sous d'autres formes dans divers langages.

---

## Module 8 — API, Web, bases de données et sources externes

**Question centrale :** comment une application échange-t-elle des informations avec d'autres systèmes ?

**Compétences :**

- Comprendre client/serveur, HTTP, URL, méthodes GET/POST et statuts de réponse.
- Consommer une API JSON avec une bibliothèque adaptée et traiter erreurs, délais d'attente et données manquantes.
- Lire ponctuellement du HTML et distinguer extraction de données et scraping ; respecter les conditions d'utilisation et la confidentialité.
- Utiliser SQLite et les bases de SQL : créer, lire, modifier, supprimer, sélectionner et joindre des données.
- Manipuler les paramètres SQL de façon sûre plutôt que construire une requête à partir d'une chaîne utilisateur.
- Comprendre les compromis entre fichier JSON, CSV et base de données.
- Découvrir l'accès à un serveur SQL comme MySQL/PostgreSQL **par principe**, sans en faire une obligation d'installation.
- Apprendre à configurer des identifiants et secrets sans les placer dans le code ni dans Git.

**Projet — Bibliothèque d'activités pédagogiques :** cataloguer des exercices, matières, niveaux et tags dans SQLite ; rechercher, filtrer, enregistrer des favoris ; importer/exporter du JSON. Extension : récupérer des données d'une API publique appropriée pour alimenter certaines activités.

**Validation :** concevoir un petit schéma de données, écrire les requêtes essentielles et intégrer au moins une source externe en gérant les erreurs.

**Transfert conceptuel :** SQL, HTTP, JSON, API et modèles de données sont directement réutilisables quel que soit le langage côté client ou serveur.

---

## Module 9 — Analyse, transformation et visualisation de données

**Question centrale :** comment transformer des données brutes en information compréhensible ?

**Compétences :**

- Nettoyer, regrouper, filtrer et agréger des jeux de données simples.
- Utiliser **NumPy** pour les tableaux numériques et comprendre les opérations vectorisées.
- Utiliser **Matplotlib** pour produire des graphiques lisibles, avec axes, légendes et titres.
- Découvrir **pandas** pour manipuler des données tabulaires et CSV/Excel.
- S'initier à **SciPy** à travers un usage concret, sans exiger de maîtriser toutes ses sous-bibliothèques.
- Connaître les bases de statistiques descriptives : moyenne, médiane, dispersion, distributions, valeurs aberrantes.
- Distinguer observation, corrélation et conclusion ; ne pas surinterpréter les données.

**Projet — Observatoire des apprentissages (données fictives) :** simuler des résultats de quiz, analyser les réussites et difficultés par notion, afficher l'évolution et produire un rapport visuel. Expliquer les limites de ce qu'on peut inférer de ces chiffres.

**Validation :** recevoir un jeu de données nouveau, en identifier les problèmes de qualité, réaliser deux analyses justifiées et produire une visualisation compréhensible.

**Transfert conceptuel :** nettoyage, agrégation, statistiques et visualisation des données ne dépendent pas de Python ; NumPy, pandas et Matplotlib sont des outils Python spécifiques.

---

## Module 10 — Interfaces et architecture d'une application utilisable

**Question centrale :** comment concevoir un outil qui ne soit plus uniquement un script de terminal ?

**Compétences :**

- Distinguer logique métier, données et interface utilisateur.
- Concevoir une interaction simple : formulaire, boutons, messages de retour, états d'erreur.
- Découvrir **Tkinter** : fenêtres, widgets, événements et gestion d'état.
- Découvrir une **API web en Python** avec FastAPI ou un équivalent, pour exposer des données ou des fonctionnalités.
- Comprendre la différence entre application graphique locale, service web, API et interface front-end.
- Reprendre un projet existant sans réécrire sa logique centrale : remplacer seulement la couche d'interface.
- Tester la logique métier indépendamment de l'interface.

**Projet — Atelier pédagogique utilisable :** donner une interface à la fabrique d'activités ou au quiz : sélectionner un thème, générer une série, consulter les résultats, exporter un fichier. **Choisir une voie principale :** mini-interface Tkinter *ou* API Python simple ; l'autre sera étudiée sous forme de découverte ciblée.

**Validation :** ajouter une nouvelle interface à une fonctionnalité existante sans mélanger règles métier, affichage et sauvegarde.

**Transfert conceptuel :** événements, séparation des responsabilités, architecture client/serveur et conception d'interface. Aucun développement JavaScript requis dans notre tutorat.

---

## Module 11 — Réseau, messages et programmation concurrente

**Question centrale :** comment plusieurs programmes ou tâches communiquent-ils et avancent-ils sans se bloquer inutilement ?

**Compétences :**

- Comprendre adresse IP, port, connexion et principe des sockets TCP.
- Écrire un **petit client et serveur locaux** ; transmettre un message et interpréter une réponse.
- Comprendre les limites des appels bloquants et les notions de concurrence et de parallélisme.
- Découvrir `threading` et `concurrent.futures`, puis `asyncio` à un niveau introductif.
- Savoir que les threads ne rendent pas automatiquement les calculs CPU plus rapides ; choisir un modèle selon la tâche et l'environnement.
- Reconnaître les problèmes de partage d'état, d'attente et d'erreurs réseau.
- Connaître les principes élémentaires de sécurité : ne pas exposer un serveur d'essai à Internet sans protection.

**Projet au choix :**

- **Jeu de devinettes en réseau local :** un serveur Python détient le nombre secret et des clients textuels envoient leurs propositions.
- **Atelier d'automatisation :** traitement de plusieurs fichiers ou appels réseau indépendants, comparaison entre version séquentielle et concurrente, avec mesure et explication des limites.

**Validation :** expliquer le trajet d'un message et faire fonctionner un échange local avec gestion des erreurs courantes ; décrire correctement un cas où la concurrence est utile et un cas où elle ne l'est pas.

**Transfert conceptuel :** client/serveur, protocole, tâches concurrentes et synchronisation sont communs au développement logiciel, mais les API et modèles d'exécution varient fortement.

---

## Module 12 — Finalisation, distribution et interopérabilité

**Question centrale :** comment remettre un logiciel à quelqu'un d'autre et le rendre maintenable ?

**Compétences :**

- Organiser un projet propre : arborescence, point d'entrée, configuration, dépendances, environnement virtuel.
- Comprendre le rôle de `pyproject.toml`, d'un paquet Python et d'une commande installable.
- Produire un guide d'installation et d'utilisation, des exemples et une liste de fonctionnalités.
- Installer le projet dans un environnement neuf ; vérifier que les données et chemins sont portables.
- Faire une passe de tests, qualité, erreurs et confidentialité avant publication.
- Découvrir plusieurs façons de **relier Python à d'autres langages** : fichiers JSON/CSV, API HTTP, sous-processus, et notions d'extensions C/C++ (`ctypes`, `pybind11` à titre illustratif).
- Comprendre la différence entre bibliothèque Python, service, exécutable et application installable. Étudier la création d'un exécutable ou d'un paquet seulement si elle sert le projet.

**Projet — Livraison d'une application :** choisir un projet précédent, publier une version installable ou lançable facilement, tester l'installation sur un second environnement et demander à une autre personne de suivre le README sans explications supplémentaires.

**Validation :** fournir un dépôt autonome dont un tiers peut comprendre l'objectif, installer les dépendances, lancer l'application et exécuter les tests.

**Transfert conceptuel :** packaging, documentation, interfaces entre composants et contrats d'échange. L'interopérabilité JS/C++ est étudiée **en Python**, sans cours parallèle de ces langages.

### Jalon B — Développer une application complète

Je dois pouvoir :

- [ ] organiser un vrai projet Python modulaire ;
- [ ] choisir comment persister les données (fichiers ou SQL) ;
- [ ] utiliser une bibliothèque externe et consulter sa documentation ;
- [ ] fournir des tests et un traitement d'erreurs sensé ;
- [ ] proposer une interface adaptée ;
- [ ] importer, transformer et visualiser des données ;
- [ ] expliquer les bases d'un échange réseau et de l'exécution concurrente ;
- [ ] préparer une version présentable à un tiers.

---

# PARTIE C — PROJET FINAL DE PORTFOLIO

## Module 13 — Concevoir et réaliser une application personnelle

Le projet final doit être **personnel, utile ou amusant, mais de taille maîtrisable**. Il mobilise plusieurs modules sans chercher artificiellement à utiliser toutes les fonctionnalités Python.

### Trois propositions adaptées à tes centres d'intérêt

#### Option A — **ClassQuest Mini : jeu pédagogique à énigmes**

Un petit jeu d'aventure où le joueur explore une carte, résout des défis de logique, collecte des objets et débloque de nouvelles zones.

- **Version essentielle :** règles du jeu, monde en données structurées, inventaire, sauvegarde JSON, gestion d'erreurs et tests.
- **Version avancée :** cartes et quêtes configurables, historique des tentatives, interface Tkinter ou autre interface simple.
- **Intérêt portfolio :** montre logique algorithmique, conception de jeu, programmation orientée objet et réflexion pédagogique.

#### Option B — **Atelier de création d'exercices pédagogiques**

Une application permettant de composer et générer rapidement des exercices à partir d'une banque de questions.

- **Version essentielle :** import CSV/JSON, filtres par matière/niveau/notion, génération d'une fiche HTML imprimable, tests.
- **Version avancée :** base SQLite, modèles d'activités, interface simple et historique des créations.
- **Intérêt portfolio :** outil concret et démontrable lié à la technopédagogie, aux données et à l'automatisation.

#### Option C — **Prototype de tuteur pédagogique à règles explicites**

Un programme qui propose des questions graduées, donne des indices prédéfinis et suit la progression sur des notions clairement délimitées.

- **Version essentielle :** parcours d'exercices, niveaux d'indices, historique local et règles de sélection documentées.
- **Version avancée :** API, base de données, statistiques ; possibilité d'intégrer ultérieurement un modèle de langage, mais ce n'est **pas nécessaire pour valider Python**.
- **Intérêt portfolio :** lien fort entre pédagogie, conception logicielle, données et futur développement de systèmes IA.

> **Recommandation :** démarrer avec *ClassQuest Mini* ou *Atelier de création d'exercices*. Le tuteur pédagogique est un excellent second grand projet, une fois les fondations du code consolidées. L'usage d'une IA générative ne doit jamais masquer une logique Python que je ne comprends pas.

### Étapes obligatoires du projet final

1. **Cadrer le problème :** utilisateur, besoin, scénario de démonstration, limites et fonctionnalités indispensables.
2. **Découper le travail :** données, logique, entrées/sorties, tests, interface.
3. **Réaliser un prototype minimal utilisable** avant d'ajouter les options.
4. **Développer par petites itérations** et commits Git explicites.
5. **Écrire des tests** sur la logique importante et traiter les erreurs prévisibles.
6. **Faire relire le code**, puis expliquer les modifications acceptées ou refusées.
7. **Documenter et livrer :** README, installation, captures pertinentes, exemple de données, limites connues.
8. **Soutenir oralement le projet :** présenter l'architecture, expliquer une fonction choisie au hasard et réaliser une petite modification en direct.

### Rubrique d'évaluation du projet

| Critère | Attendu |
|---|---|
| Utilité et fonctionnement | Le scénario central fonctionne réellement. |
| Autonomie | Je sais expliquer et modifier le code essentiel. |
| Architecture | Le programme est découpé de façon cohérente. |
| Qualité | Noms explicites, gestion des erreurs, tests pertinents. |
| Données | Formats et stockage adaptés ; respect des données personnelles. |
| Documentation | Une autre personne peut installer et utiliser le projet. |
| Réflexion | Je sais identifier limites, compromis et prochaines améliorations. |

**Livrable portfolio :** un dépôt Git public ou partageable, avec README soigné, tests, démonstration et bilan personnel précisant ce qui a été conçu et codé par moi, et comment l'IA a été utilisée.

---

## 3. Les projets comme fil conducteur (et non comme décoration)

Nous n'allons pas produire treize projets artificiellement séparés. L'idée est de combiner :

- **Micro-défis ciblés** pour apprendre une notion : fonctions, collections, regex, classes, etc.
- **Deux projets évolutifs :** *moteur de quiz / atelier pédagogique* et *jeu d'exploration / mini-RPG*.
- **Un projet données :** *observatoire des apprentissages fictifs*.
- **Un projet final choisi librement**, suffisamment abouti pour le portfolio.

Un projet n'est pas obligé d'intégrer chaque nouvelle notion. Nous apprendrons aussi à reconnaître quand une abstraction, une classe ou une bibliothèque **n'apporte rien**.

### Mini-projets optionnels pour varier les plaisirs

| Envie du moment | Projet court | Notions mobilisées |
|---|---|---|
| Jeu vidéo | Générateur de donjon en grille | Collections, fonctions, hasard, algorithmes |
| Logique | Résolveur de casse-tête simple | Recherche, récursivité, tests |
| Pédagogie | Générateur de cartes de révision | Fichiers, dictionnaires, templating |
| Création | Art génératif ou motifs géométriques | Boucles, fonctions, bibliothèques graphiques |
| Bricolage utile | Organisateur de fichiers avec mode simulation | `pathlib`, exceptions, sécurité, CLI |
| Travail quotidien | Convertisseur de données CSV ↔ JSON | Parsing, validation, tests |
| Expérimentation | Simulation de probabilités de dés | `random`, statistiques, Matplotlib |

Chaque projet doit répondre à une question : **qu'ai-je réellement appris à faire seul ?**

---

## 4. Le protocole de nos séances de tutorat

### Déroulement d'une leçon (15 à 35 minutes, cours compris)

1. **Le cours :** ce qu'on va faire, à quoi ça sert, un exemple concret et sa sortie, ce qu'il faut retenir, l'objectif du jour. Une notion déjà connue a son cours express.
2. **Questions :** je pose les miennes, puis « C'est parti ! ».
3. **Pratique guidée :** des consignes dans l'éditeur, un retour à chaque lancement ; des QCM pour vérifier la compréhension ; les révisions dues, de mémoire.
4. **Pratique seule :** le même type de tâche, sans appui.
5. **Transfert :** une tâche nouvelle qui mobilise la notion autrement.
6. **Bilan :** ce que je sais faire seul, ce qui reste fragile, les révisions à prévoir ; je valide la leçon.

Un mini-projet prend 45 à 90 minutes, en une ou plusieurs séances. Ces durées sont des repères pour doser la pratique, pas des limites.

**Répondre en un clic :** tout ce qui se choisit se clique — un QCM quand une seule réponse est juste, des boutons pour oui ou non et pour la suite. J'écris dans la discussion pour expliquer avec mes mots ou poser une question ; le code s'écrit dans l'éditeur.

### Mes niveaux d'aide

| Niveau | Ce que fait le tuteur |
|---|---|
| 0 — Autonomie | Pose le problème et me laisse chercher. |
| 1 — Question | M'aide à clarifier mon raisonnement. |
| 2 — Indice | Rappelle le concept pertinent, sans écrire la réponse. |
| 3 — Guidage | Découpe le problème et propose du pseudo-code. |
| 4 — Exemple ciblé | Montre un petit exemple analogue, pas la solution complète. |
| 5 — Correction explicite | Explique la solution complète **si nécessaire ou demandée**, puis me fait refaire une variante seul. |

**Règle de qualité :** le tuteur ne transforme pas chaque question en interrogation interminable. Une explication claire et directe est appropriée lorsque je demande un rappel, une définition ou une démonstration. Les indices progressifs servent surtout quand je suis en train de résoudre un exercice.

### Suivi léger des acquis

Après chaque module, maintenir un journal sous cette forme :

```markdown
## Module X — [nom]
- Statut : à découvrir / en cours / acquis / à consolider
- Je sais faire seul :
- Difficultés observées :
- Exercices ou projets réalisés :
- Test de maîtrise : réussi / à refaire
- Notion à réactiver dans un prochain module :
- Lien conceptuel avec d'autres langages, si utile :
```

### Utilisation de l'IA pour les projets

- **Pendant l'apprentissage :** l'IA est prioritairement un tuteur, pas un générateur de solution.
- **Pendant le débogage :** décrire d'abord mon hypothèse et les tests effectués ; demander un indice ou une revue ciblée.
- **Pendant un projet personnel :** l'IA peut proposer des idées, critiquer l'architecture et expliquer la documentation ; toute contribution générée doit être relue, testée et comprise.
- **Pendant les évaluations :** pas de génération du code demandé. Documentation Python et messages d'erreur autorisés.

---

## 5. Une progression qui reste transférable, sans mélanger les apprentissages

Nos séances **ne comprendront pas d'exercices JavaScript ou C++**. Je les travaille séparément, à mon rythme. Les correspondances servent uniquement à construire une culture générale de programmation.

Nous ferons une courte mise en perspective lorsque cela éclaire réellement la notion :

| Idée générale | Exemple de lien utile | Vigilance |
|---|---|---|
| Conditions, boucles et fonctions | Le raisonnement algorithmique est réutilisable. | La syntaxe n'est pas interchangeable. |
| Listes, tableaux, dictionnaires, objets | Tous les langages doivent organiser les données. | Types, mutabilité, performances et usages diffèrent. |
| Modules et bibliothèques | Un projet doit maîtriser les dépendances et les responsabilités. | Les systèmes de modules et de compilation diffèrent. |
| Classes et objets | On retrouve encapsulation, composition et polymorphisme. | Les modèles objet de Python, JS et C++ ne sont pas identiques. |
| Exceptions et erreurs | Une application doit anticiper les échecs. | Les mécanismes précis de gestion d'erreurs diffèrent. |
| JSON, HTTP, SQL et API | Ce sont des interfaces partagées entre technologies. | Ne pas confondre protocole ou format et langage. |
| Concurrence | Les programmes attendent, échangent et coordonnent des tâches. | Threads, processus et boucles d'événements ont des comportements distincts. |
| Tests, Git et documentation | Ce sont des habitudes professionnelles transversales. | Les outils concrets changent selon l'écosystème. |

**Principe de transfert :** à la fin d'un module, une question facultative pourra être : « Quelle idée générale pourrais-tu reconnaître dans un autre langage ? » La réponse restera conceptuelle, sans devoir apprendre une deuxième syntaxe.

---

## 6. Correspondance avec les objectifs IFAGE Python 1 et 2

Ce parcours **couvre les grandes familles de compétences** annoncées dans les deux programmes IFAGE, mais **ne prétend pas délivrer leur certificat ni reproduire leurs modalités d'évaluation**. Il les réordonne pour privilégier les compétences durables et les projets.

| Compétence figurant dans les programmes IFAGE | Où nous la travaillons |
|---|---|
| Types, opérateurs, conditions, boucles | Modules 0, 1 et 2 |
| Chaînes, listes, tuples, ensembles, dictionnaires | Modules 0, 2 et 3 |
| Fonctions, paramètres, retours, récursivité | Modules 0, 3 et 7 |
| Dates et heures | Module 4 |
| Modules, bibliothèques, architecture | Modules 0, 5 et 12 |
| Fichiers, CSV, JSON, XML, exceptions | Modules 0 et 4 ; réutilisation en 8–9 |
| Expressions régulières | Module 4 |
| Classes, encapsulation, héritage, polymorphisme | Modules 0 et 6 |
| Surcharge d'opérateurs et méthodes spéciales | Module 6 |
| Lambda, `map`, `filter`, `reduce` | Module 7 |
| Fonctions imbriquées, closures, générateurs, décorateurs | Module 7 |
| Bases de données et sources Internet | Module 8 |
| NumPy, SciPy, Matplotlib, traitement/visualisation | Module 9 |
| Interfaces graphiques Tkinter | Module 10 |
| Programmation web/serveur | Modules 8 et 10 : API modernes |
| Sockets, client/serveur, messages, multithreading | Module 11 |
| Intégration Python avec Java/C/C++ et autres systèmes | Module 12 : panorama et exemple ciblé |
| Création/distribution d'applications | Modules 12–13 |
| Maintenance de code existant et projet complet | Modules 5, 6 et 13 |

### Actualisation volontaire du programme

- **CGI :** le module `cgi` de la bibliothèque standard a été supprimé à partir de Python 3.13. Nous expliquerons brièvement l'idée historique, puis travaillerons le développement web moderne à travers HTTP et une API Python. [Documentation officielle Python](https://docs.python.org/3/whatsnew/3.13.html#removed-modules-and-apis).
- **SQLite d'abord :** permet d'apprendre SQL sans ajouter dès le départ un serveur de base de données. L'accès à MySQL/PostgreSQL est présenté ensuite comme extension possible.
- **Tests, Git, environnement virtuel et qualité :** ajoutés explicitement car ils sont essentiels à des projets que l'on veut maintenir.
- **pandas, types et interfaces d'API :** ajouts pertinents pour les données et pour une éventuelle spécialisation IA.
- **Mathématiques pour l'IA :** les notions statistiques du module 9 servent de passerelle. L'algèbre linéaire, les probabilités et d'autres prérequis mathématiques approfondis feront idéalement l'objet d'un **parcours complémentaire**, et non d'un remplacement du cursus Python.

---

## 7. Réussir son portfolio sans multiplier les projets inachevés

Mieux vaut **deux ou trois projets bien finis** que dix démonstrations incomplètes.

### Pour chaque projet retenu

- [ ] Une description du problème et des personnes auxquelles l'outil s'adresse.
- [ ] Des instructions pour lancer le programme.
- [ ] Une démonstration concrète (capture, courte vidéo ou exemple reproductible).
- [ ] Des tests exécutables et des données d'exemple **fictives** si nécessaire.
- [ ] Un historique de développement compréhensible.
- [ ] Une section « Choix techniques et difficultés rencontrées ».
- [ ] Une indication transparente des usages de l'IA pendant la conception.
- [ ] Aucune donnée réelle d'élèves, aucun mot de passe ni clé secrète dans le dépôt.

**Portfolio cible :**

1. **Un jeu pédagogique jouable** : preuve de logique, algorithmes et conception.
2. **Un outil pédagogique ou d'automatisation utile** : preuve de manipulation de fichiers/données et d'architecture.
3. **Une application plus avancée** avec stockage, API, visualisations ou interface : preuve d'intégration et de capacité à livrer.

Ces projets peuvent évoluer depuis les modules précédents : il n'est pas nécessaire d'en créer trois nouveaux en fin de formation.

---

## 8. Après le module 0

Le mini-projet du nombre mystère sert de bilan du module 0. Ce qu'il montre oriente la suite :

- une notion maîtrisée a, dans les modules 1 à 3, un cours express et une pratique courte ;
- une notion fragile, notée dans la mémoire du cours, revient en révision et au début du module qui l'approfondit ;
- aucun module n'est sauté d'office : on avance par maîtrise, démontrée sur des problèmes nouveaux.

---

## 9. Sources et références

**Programmes qui servent de référence aux objectifs :**

- IFAGE, *Apprenez à programmer en Python — niveau 1*, descriptif officiel (consulté le 24 octobre 2026) : https://www.ifage.ch/formation/tic/formations-programmation/apprenez-a-programmer-en-python/
- IFAGE, *Apprenez à programmer en Python — niveau 2*, descriptif officiel (consulté le 24 octobre 2026) : https://www.ifage.ch/formation/tic/formations-programmation/programmeur-python-niveau-2/

**Documentation technique de référence :**

- Python Software Foundation, *Le tutoriel Python* : https://docs.python.org/fr/3/tutorial/
- Python Software Foundation, *Bibliothèque standard Python* : https://docs.python.org/fr/3/library/
- Python Software Foundation, *What's New in Python 3.13 — Removed Modules and APIs* : https://docs.python.org/3/whatsnew/3.13.html#removed-modules-and-apis
- Documentation pytest : https://docs.pytest.org/
- Documentation NumPy : https://numpy.org/doc/
- Documentation Matplotlib : https://matplotlib.org/stable/
- Documentation FastAPI : https://fastapi.tiangolo.com/

**Nature de ce document :** parcours pédagogique original proposé pour un apprentissage accompagné. Les modules, les projets, les critères de validation et leur ordre sont des **choix de conception pédagogique**, et non une reproduction officielle du syllabus IFAGE ou une promesse de niveau certifié.
