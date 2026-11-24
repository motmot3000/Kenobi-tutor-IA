# La ligne de commande et Git : premiers pas

Un cours court pour se sentir chez soi dans un terminal, puis garder l'historique de son code avec Git. Pour
quelqu'un qui n'a jamais ouvert de terminal, ou qui tape des commandes sans trop savoir ce qu'elles font.

**Niveau :** débutant
**Langage et outils :** ligne de commande (bash ou zsh sous Linux et macOS, PowerShell sous Windows) et Git, dans le
terminal de Kenobi

## Objectifs du cours

À la fin, l'apprenant sait :

- se repérer dans ses dossiers et y créer, copier, déplacer et supprimer des fichiers sans interface graphique ;
- lire un message d'erreur du terminal et en tirer quoi corriger ;
- lancer un programme et rediriger ce qu'il affiche dans un fichier ;
- suivre l'évolution d'un projet avec Git : dépôt, commits, historique, retour en arrière ;
- travailler sur une branche, puis la fusionner ;
- expliquer avec ses mots ce que fait chaque commande qu'il tape.

## Module 1 — Se repérer dans le terminal

Le terminal comme une autre façon de voir ses dossiers : où l'on est, ce qu'il y a, comment se déplacer.

### Leçon 1 — Où suis-je ?

- Objectif : dire dans quel dossier on se trouve et ce qu'il contient.
- Notions : invite de commande, dossier courant, `pwd`, `ls` (`dir` sous PowerShell), options `-l` et `-a`.
- Exercice type : afficher le dossier courant, puis la liste détaillée de son contenu, fichiers cachés compris.

### Leçon 2 — Se déplacer

- Objectif : aller dans n'importe quel dossier et revenir, par un chemin relatif ou absolu.
- Notions : `cd`, `..`, `~`, chemin relatif et absolu, touche Tab pour compléter.
- Exercice type : depuis son dossier personnel, aller dans un sous-dossier, remonter de deux niveaux, revenir chez soi.

### Leçon 3 — Créer et ranger

- Objectif : créer une petite arborescence de projet et y ranger des fichiers.
- Notions : `mkdir -p`, `touch` (`New-Item` sous PowerShell), `cp`, `mv`, `rm`, prudence avec `rm -r`.
- Exercice type : créer `projet/src` et `projet/docs`, y placer deux fichiers, en renommer un, en supprimer un.

### Leçon 4 — Lire des fichiers et chercher

- Objectif : afficher un fichier et retrouver une ligne dans plusieurs fichiers.
- Notions : `cat`, `head`, `tail`, `less`, `grep` (et `Select-String` sous PowerShell).
- Exercice type : trouver dans quels fichiers d'un dossier apparaît un mot, avec le numéro de ligne.

## Module 2 — Faire travailler le terminal

Lancer des programmes, enchaîner les commandes et comprendre ce qui se passe quand ça échoue.

### Leçon 1 — Lancer un programme et lire une erreur

- Objectif : lancer un script et expliquer le message d'erreur qu'il produit.
- Notions : `python3 script.py` ou `node script.js`, sortie standard et sortie d'erreur, code de retour `$?`.
- Exercice type : lancer un script qui échoue, dire à quelle ligne et pourquoi, sans le corriger.

### Leçon 2 — Rediriger et enchaîner

- Objectif : garder la sortie d'une commande dans un fichier et en enchaîner plusieurs.
- Notions : `>`, `>>`, `|`, `wc -l`, `sort`, `&&`.
- Exercice type : compter les lignes d'un fichier qui contiennent un mot, et écrire le résultat dans `rapport.txt`.

### Leçon 3 — Mini-projet : ranger un dossier en désordre

- Objectif : remettre en ordre un dossier réel avec des commandes, en expliquant chaque étape.
- Notions : tout le module 1 et le module 2.
- Exercice type : à partir d'un dossier fourni, classer les fichiers par type dans des sous-dossiers et produire la
  liste de ce qui a été déplacé.

## Module 3 — Garder l'historique avec Git

Un projet qui se souvient de chaque étape : pouvoir essayer sans peur de tout casser.

### Leçon 1 — Un dépôt et un premier commit

- Objectif : créer un dépôt et enregistrer une première version d'un projet.
- Notions : `git init`, `git status`, `git add`, `git commit -m`, configuration du nom et de l'adresse.
- Exercice type : mettre un petit projet sous Git et faire un premier commit au message clair.

### Leçon 2 — Lire l'historique et revenir en arrière

- Objectif : retrouver ce qui a changé et annuler une modification.
- Notions : `git log --oneline`, `git diff`, `git restore`, `git show`.
- Exercice type : casser un fichier, voir la différence, puis le remettre dans son état du dernier commit.

### Leçon 3 — Travailler sur une branche

- Objectif : essayer une idée sur une branche, puis la fusionner.
- Notions : `git switch -c`, `git branch`, `git merge`, ce qu'est un conflit et comment le résoudre.
- Exercice type : ajouter une fonction sur une branche, la fusionner dans la branche principale, résoudre un petit
  conflit volontaire.

### Leçon 4 — Mini-projet : l'historique d'un vrai petit projet

- Objectif : mener un petit projet de bout en bout avec des commits réguliers et lisibles.
- Notions : tout le module 3, messages de commit, `.gitignore`.
- Exercice type : écrire un script en quatre étapes, un commit par étape, puis raconter l'histoire du projet à partir
  de `git log`.

## Évaluation

Le cours est réussi quand l'apprenant, seul et sur un dossier qu'il ne connaît pas, sait s'y repérer, le réorganiser
avec des commandes qu'il explique, et mettre le résultat sous Git avec un historique propre de trois commits au moins.
