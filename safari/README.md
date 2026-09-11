# Archivage X pour Safari sur Mac

Ce dossier fournit l'adaptateur natif Safari et un script qui produit le projet
Xcode avec l'outil officiel d'Apple. L'interface et la logique d'archivage restent
les mêmes sources WebExtensions que sur Chrome et Firefox.

**État de validation :** génération et contrôles Python testables sur Windows et
Linux ; compilation Swift, installation, autorisations Safari, lecture des deux
cookies X et archivage réel à valider sur un Mac. Ce dossier ne constitue pas un
binaire macOS signé, ni une publication dans l'App Store. iPhone/iPad ne sont pas
ciblés : le service de téléchargement est une application de bureau.

## Construire sur un Mac

Installer la version complète de Xcode, ouvrir Xcode une fois pour accepter sa
licence et sélectionner ses outils de développement. À la racine du projet :

```sh
python3 build_extensions.py --browser safari --output dist/extensions
python3 safari/prepare_safari.py --extension dist/extensions/safari --output build/safari-xcode
```

Le script choisit `safari-web-extension-packager` quand disponible, sinon son
ancien nom `safari-web-extension-converter`. Il copie les seules ressources du
build Safari, crée un projet macOS, remplace le gestionnaire de messages natifs
et autorise son accès réseau sortant. Le projet de sortie doit être nouveau pour
protéger les réglages de signature et les modifications effectuées dans Xcode.

L'option `--build` effectue aussi une compilation Debug sans signature, utile
pour détecter des erreurs de compilation. Un succès de cette commande ne signifie
pas que Safari autorise déjà l'extension : terminer la signature et l'installation
dans Xcode. `--bundle-id` permet d'utiliser son propre identifiant, à choisir avant
une distribution ; `org.archivex.app` est la valeur locale par défaut.

Dans le projet Xcode généré, choisir **Signing & Capabilities** pour les deux
cibles (application et extension). Pour un test local, utiliser **Sign to Run
Locally**, puis autoriser les extensions non signées dans le menu de développement
de Safari. Compiler et lancer la cible application. Dans les réglages Extensions
de Safari, activer Archivage X et autoriser son accès à X et à la page locale
d'archivage. Selon Safari, l'autorisation de développement doit être renouvelée
après une fermeture du navigateur ; ce n'est pas un mécanisme de distribution.

## Utiliser

1. Installer et lancer le compagnon **Archivage X pour macOS** produit séparément
   par ce projet. Il doit servir `http://127.0.0.1:18765`.
2. Ouvrir cette adresse dans Safari et connecter l'extension à l'archive.
3. Se connecter à X dans Safari, puis utiliser « Utiliser ma connexion X ».
4. Archiver un post depuis X, vérifier sa catégorie, ses médias et son export.

Le compagnon doit être lancé sur le Mac avant utilisation. Le gestionnaire natif
Safari vérifie uniquement `/health` ; il renvoie `companion_required` si le
compagnon est arrêté. L'extension explique alors de l'ouvrir puis de réessayer.
Elle ne prétend pas démarrer un processus Python depuis le sandbox Safari.
Le lancement automatique disponible avec l'hôte Chrome/Firefox n'est donc pas
annoncé pour cette première version Safari.

Safari ignore le nom d'application passé à `sendNativeMessage` : il remet le
message au gestionnaire natif de son application conteneur. Aucun manifeste
d'hôte Chrome/Firefox ne s'installe pour Safari. Le gestionnaire accepte seulement
`ping` et `start`, vérifie l'identité du service local, et ne lit aucun fichier
de session ni d'archive. Les données restent dans le dossier du compagnon.

## Vérification avant distribution

- Compiler et tester sur les architectures macOS que l'on veut distribuer.
- Tester connexion initiale, refus puis octroi des permissions de sites,
  cookies de session, archivage d'une photo et d'une vidéo, catégories et export.
- Fermer le compagnon, vérifier le message de relance manuelle, le redémarrer et
  vérifier que la connexion retrouve l'archive sans perdre les catégories.
- Tester un redémarrage du Mac et Safari, et vérifier le dossier réel des données.
- Pour distribuer sans mode développement, configurer signature, profil et canal
  de distribution Apple, puis tester **le paquet distribué** sur un second Mac.
  Une publication App Store doit aussi examiner le compagnon séparé et ses
  contraintes de sandbox ; ce script n'effectue pas cette publication.

Ne jamais ajouter données collectées, cookies, clés de pont ou environnement
Python personnel au dossier de ressources de l'extension. Les installateurs
doivent partir d'une configuration propre, avec une session X propre à chaque Mac.

## Références Apple

- [Packaging a web extension for Safari](https://developer.apple.com/documentation/safariservices/packaging-a-web-extension-for-safari)
- [Messaging between the app and JavaScript in a Safari web extension](https://developer.apple.com/documentation/safariservices/messaging-between-the-app-and-javascript-in-a-safari-web-extension)
- [Running your Safari web extension](https://developer.apple.com/documentation/safariservices/running-your-safari-web-extension)
