# Archivage X — installation sur une autre machine

L’outil comprend une extension et un service local. Le service garde les posts,
les médias et les CSV sur votre machine. Installez les deux éléments. La session X
doit être celle du navigateur utilisé sur cette machine ; aucune session du
développeur n’est fournie. Le stockage n’est pas synchronisé entre les ordinateurs.

## Windows avec Chrome, Edge ou Brave

Le paquet Windows autonome contient `zevent-archivage.exe` et ses dépendances.
Extrayez le dossier entier dans un emplacement durable et accessible en écriture.
N’exécutez pas le programme à l’intérieur du ZIP et ne déplacez pas uniquement l’EXE.

1. Lancez `INSTALLER D ABORD.cmd` une fois, depuis ce dossier.
2. Ouvrez `chrome://extensions` (ou la page des extensions du navigateur choisi),
   activez le mode développeur et chargez le dossier `chrome-extension`.
3. Connectez-vous à X dans ce navigateur, puis ouvrez `http://127.0.0.1:18765`.
4. Autorisez l’accès de l’extension à X et à la page locale si le navigateur le demande.

`DEMARRER.cmd` ouvre l’application. Le démarrage à la demande depuis l’extension
est également prévu. Si vous déplacez le dossier, relancez l’installation : elle
enregistre les chemins de cette machine. Aucune lettre de lecteur D: n’est imposée.
Windows peut signaler cet exécutable non signé ; cette distribution locale n’est
pas publiée sur un magasin d’applications.

## Firefox sur Windows ou macOS

Si vous utilisez le paquet **source sous Windows** plutôt que le paquet EXE,
installez Python 3.12 avec son lanceur `py`, puis exécutez `INSTALLER.cmd` et
`DEMARRER.cmd`. Chargez ensuite le dossier de l’extension choisie. Le Python et
les dépendances sont nécessaires seulement dans ce mode source.

Installez d’abord le service local comme indiqué pour votre système. Son installateur
enregistre également le pont Firefox, avec un identifiant distinct et stable.

Pour tester cette version non signée : ouvrez `about:debugging#/runtime/this-firefox`,
cliquez sur « Charger un module complémentaire temporaire » et choisissez
`firefox-extension/manifest.json`. Connectez-vous à X dans Firefox, puis ouvrez
la page locale. Accordez les permissions pour X et pour localhost.

Cette installation temporaire disparaît au redémarrage de Firefox. Pour une
installation permanente dans Firefox standard, le ZIP doit être signé par Mozilla
(distribution publique ou non répertoriée). Le paquet fourni n’est pas signé ;
il ne faut pas désactiver les protections du navigateur. Firefox 140 minimum.

Les permissions de données déclarent le passage des liens, contenus et deux cookies
d’authentification au service local. Aucune télémétrie ni serveur de stockage tiers
n’est utilisé par cette extension. Le service contacte X pour récupérer les posts.

## macOS : application locale depuis les sources

Le paquet source inclut les scripts macOS ; l’EXE Windows ne fonctionne pas sur Mac.
Installez Python 3.12 depuis python.org. Décompressez le dossier dans votre dossier
personnel. Dans Terminal, placez-vous dans ce dossier puis lancez :

```sh
sh installer.command
sh demarrer.command
```

Le premier script crée un environnement Python propre à ce Mac, installe les
versions de dépendances listées et enregistre les ponts Chrome/Firefox. Ne copiez
pas l’environnement `.venv` du PC. Le second démarre le service et ouvre sa page.
Pour Chrome sur Mac, chargez ensuite `chrome-extension` comme sous Windows.
Pour Safari, suivez aussi la section suivante. Les résultats peuvent être placés
dans le dossier choisi dans l’interface ; le sélecteur macOS utilise le dialogue natif.

Pour fabriquer une distribution autonome sur Mac sans Python chez le destinataire :

```sh
.venv/bin/python -m pip install pyinstaller
.venv/bin/python build_exe.py --output dist/mac-release
```

La construction doit être exécutée sur le système et l’architecture à distribuer
(Apple Silicon ou Intel). La signature/notarisation pour diffusion macOS reste une
étape de publication à réaliser avec votre compte Apple. Les sources ne prétendent
pas fournir un binaire universel macOS déjà validé.

## Safari sur Mac

Safari ne charge pas directement le dossier Chrome. Une application contenant
l’extension doit être construite avec Xcode sur Mac. Les sources et l’adaptateur
sont dans `safari/` ; consultez son README pour les commandes exactes.

Dans cette première version Safari, lancez `demarrer.command` (ou l’application
locale autonome) avant l’archivage. Le pont Safari vérifie que le service répond ;
il n’exécute pas arbitrairement Python depuis le bac à sable de l’extension.
Il indique clairement quand l’application locale doit être ouverte.

La conversion Xcode, l’activation dans Safari, les accès aux sites, la lecture des
deux cookies X et un archivage réel doivent être vérifiés sur Mac. Cette distribution
ne contient pas encore d’application Safari compilée/signée et testée sur matériel Apple.

## Construire les paquets à partir du dépôt

```sh
python build_extensions.py --browser all --output dist/extensions-nouvelle-version
python build_distribution.py --output dist/archivage-x-sources-nouvelle-version
python build_exe.py --output dist/windows-release-nouvelle-version
```

Les dossiers de sortie doivent être nouveaux. Les paquets excluent les archives,
les bases de session, les profils, les cookies et les chemins natifs déjà installés.
Une configuration neuve d’archivage manuel est fournie ; elle ne lance aucune recherche
automatique. Les installations existantes et leurs catégories sont conservées.
Les médias restent identiques et les exports conservent leurs deux tableaux CSV.

Pour relever les codecs, dimensions, durées et fréquences d’images mesurés dans
les fichiers vidéo, installez aussi FFmpeg/ffprobe sur la machine et rendez
`ffprobe` accessible dans le PATH (ou indiquez son chemin dans `config.yaml`).
Sans lui, les fichiers sont conservés mais la validation technique est indiquée
`ffprobe_unavailable` ; elle ne doit pas être interprétée comme une vérification réussie.
L’option `--self-test` du programme autonome vérifie hors connexion ses dépendances,
les empreintes d’image et la lecture/écriture Parquet ; elle n’interroge pas X.

## Vérification avant diffusion

Sur chaque combinaison annoncée : installer, ouvrir X, archiver un post choisi,
attendre son média, vérifier le classement et la lecture, exporter vers un dossier
choisi, redémarrer navigateur/service et vérifier la reconnexion. Les tests simulés
vérifient les contrats d’API mais ne remplacent pas ces contrôles sur le navigateur réel.

Sources techniques :
- Mozilla, compatibilité : https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Build_a_cross_browser_extension
- Mozilla, pont natif : https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging
- Mozilla, données : https://extensionworkshop.com/documentation/develop/firefox-builtin-data-consent/
- Apple, Safari Web Extensions : https://developer.apple.com/safari/extensions/

## Stockage des archives

Aucun choix de dossier n’est nécessaire au premier lancement. Une nouvelle installation range automatiquement les archives dans le profil personnel :

- Windows : `%LOCALAPPDATA%\Archivage X\archives`.
- macOS : `~/Library/Application Support/Archivage X/archives`.
- Linux : `${XDG_DATA_HOME:-~/.local/share}/archivage-x/archives`.

Une archive existante reste à son emplacement. Le choix est mémorisé indépendamment du dossier du programme. Dans « Stockage des archives », « Ouvrir le dossier » affiche les fichiers ; « Modifier… » permet de choisir un dossier vide. L’application termine l’opération en cours, copie les données, vérifie chaque fichier et les bases, puis utilise le nouveau dossier. L’ancien dossier est conservé. Si la copie échoue, le dossier actuel reste utilisé et la copie partielle est conservée pour diagnostic.

Le dossier des résultats CSV et médias se choisit séparément dans la zone d’export. L’import `archive_urls.py liste.txt` retrouve le même stockage ; `--config` permet de désigner explicitement une autre configuration pour un usage avancé. Si un dossier choisi devient inaccessible, le lancement signale le problème sans créer silencieusement une nouvelle archive vide.
