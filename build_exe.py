#!/usr/bin/env python3
"""Construire l'executable du serveur d'archivage.

Produit `dist/zevent-archivage/`, un dossier autonome : l'utilisateur n'a ni
Python ni dependances a installer. L'interface locale et une configuration par
defaut sont embarquees ; la configuration modifiable, la base et les medias
restent dans le profil personnel, independamment du programme.

Le format « un dossier » est prefere au fichier unique : ce dernier se deplie
dans un repertoire temporaire a chaque lancement, ce qui coute plusieurs
secondes au demarrage et fait regulierement reagir les antivirus.

    python build_exe.py            construit
    python build_exe.py --clean    reconstruit de zero
"""
import argparse
import shutil
import subprocess
import sys
from datetime import datetime
import build_extensions
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = 'zevent-archivage'
LISEZ_MOI = "Archivage X\r\n================\r\n\r\n1. Double-cliquez sur INSTALLER D ABORD.cmd\r\n   Il prepare la connexion avec Chrome, demarre le service et ouvre la page.\r\n\r\n2. Dans Chrome, ouvrez chrome://extensions\r\n   Activez le Mode developpeur, en haut a droite.\r\n   Cliquez sur Charger l'extension non empaquetee.\r\n   Choisissez le dossier chrome-extension, ici meme.\r\n\r\n3. Connectez-vous a X dans ce navigateur.\r\n   L'extension se sert de votre connexion a X pour recuperer les posts.\r\n   Elle ne lit que deux cookies, et rien d'autre.\r\n\r\nC'est tout. Un bouton d'archivage apparait sous chaque post X.\r\n\r\nEnsuite il n'y a plus rien a lancer : l'extension demarre le service\r\nquand elle en a besoin, meme apres un redemarrage du PC.\r\n\r\nDEMARRER.cmd ouvre la page sans passer par X.\r\nDESINSTALLER.cmd retire la connexion avec Chrome. Vos archives restent.\r\n\r\n\r\nChaque post est range dans une categorie, ZEVENT 2026 par defaut. Vous en\r\nchoisissez une au moment d'archiver, et le dernier choix est repris ensuite,\r\ndonc le bouton reste un seul clic. Sur la page locale, la pastille d'une\r\narchive la deplace, le filtre n'affiche qu'une categorie, et l'export peut\r\ns'y limiter. Un post garde pour un autre usage n'a donc plus a etre detruit.\r\n\r\nVos donnees vivent dans le dossier data, ici meme. Deplacer ce dossier\r\ndeplace toute l'archive.\r\n\r\n\r\nCe que fait cet outil, et ce que cela implique\r\n----------------------------------------------\r\n\r\nL'extension ajoute un bouton sous chaque post X. Quand vous cliquez, un\r\nservice qui tourne sur votre machine telecharge le post : texte,\r\ncompteurs, media, et la reponse complete renvoyee par X. L'extension lit\r\ndeux cookies de votre session (auth_token et ct0) et les transmet a ce\r\nservice. Ces valeurs ne quittent jamais votre machine.\r\n\r\nVous archivez avec votre propre compte, et X voit votre session derriere\r\nchaque telechargement. L'outil interroge X par ses adresses internes, les\r\nmemes que le site utilise, plutot que par une interface de programmation\r\ndocumentee. Cet acces automatise ne correspond pas a l'usage prevu par\r\nles conditions de X, et le risque, s'il se materialise, porte sur votre\r\ncompte. L'outil a ete concu pour de la recherche universitaire sur un\r\ncorpus delimite. La collecte de masse n'est pas son objet.\r\n"
DIST = ROOT / 'dist'
BUILD = ROOT / 'build'

# twscrape et curl_cffi chargent une partie de leurs modules dynamiquement :
# sans ces indications, l'analyse statique de PyInstaller les manque.
HIDDEN = ['twscrape', 'twscrape.api', 'twscrape.models', 'twscrape.utils',
          'curl_cffi', 'curl_cffi.requests', 'imagehash', 'PIL.Image', 'pyarrow',
          'pyarrow.parquet', 'yaml', 'dotenv', 'httpx', 'aiosqlite',
          # Charges seulement selon le mode, donc invisibles a l'analyse statique.
          'native_host.host', 'install_native_host', 'server_control',
          'summary_index', 'summaries', 'remove_post', 'webbrowser', 'runtime_check', 'scipy.fftpack']

# Rien de ce qui suit n'est utile au serveur, et pyarrow.tests pese lourd.
EXCLUDED = ['tkinter', 'matplotlib', 'scipy.tests', 'pytest', 'pyarrow.tests',
            'numpy.tests', 'PIL.ImageQt']


def write_launchers(target):
    """Un double-clic pour installer, un autre pour ouvrir la page."""
    if sys.platform != 'win32':
        for name, arg in (('installer.sh', '--install'), ('demarrer.sh', '')):
            script = target / name
            script.write_text('#!/bin/sh\ncd "$(dirname "$0")"\n'
                              'exec ./%s %s\n' % (NAME, arg), encoding='utf-8')
            script.chmod(0o755)
        return
    launchers = {
        'INSTALLER D ABORD.cmd':
            '@echo off\r\ncd /d "%~dp0"\r\n"{name}.exe" --install\r\necho.\r\npause\r\n',
        'DEMARRER.cmd':
            '@echo off\r\ncd /d "%~dp0"\r\nstart "" "{name}.exe"\r\n'
            'timeout /t 3 /nobreak >nul\r\nstart "" http://127.0.0.1:18765\r\n',
        'DESINSTALLER.cmd':
            '@echo off\r\ncd /d "%~dp0"\r\n"{name}.exe" --uninstall\r\npause\r\n',
    }
    for filename, body in launchers.items():
        (target / filename).write_text(body.format(name=NAME), encoding='utf-8')
    (target / 'LISEZ-MOI.txt').write_text(LISEZ_MOI, encoding='utf-8')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--clean', action='store_true', help='repartir de zero')
    ap.add_argument('--output', type=Path, default=DIST / ('release-' + datetime.now().strftime('%Y%m%d-%H%M%S')))
    ap.add_argument('--work-dir', type=Path, default=BUILD / 'portable')
    args = ap.parse_args()
    destination = args.output.resolve()
    work = args.work_dir.resolve()
    if destination.exists():
        ap.error('Choisissez un nouveau dossier --output pour protéger les installations existantes.')

    if args.clean:
        ap.error('--clean désactivé : choisissez un nouveau dossier --work-dir.')

    separator = ';' if sys.platform == 'win32' else ':'
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir',
               '--name', NAME, '--console',
               '--add-data', f'{ROOT / "manual_ui"}{separator}manual_ui',
               '--add-data', f'{ROOT / "packaging" / "config.yaml"}{separator}.',
               '--add-data', f'{ROOT / "packaging" / "streamers.json"}{separator}.',
               '--distpath', str(destination), '--workpath', str(work),
               '--specpath', str(work)]
    for module in HIDDEN:
        command += ['--hidden-import', module]
    for module in EXCLUDED:
        command += ['--exclude-module', module]
    command.append(str(ROOT / 'archive_server.py'))

    print('  construction en cours, comptez quelques minutes…', flush=True)
    result = subprocess.run(command)
    if result.returncode:
        return result.returncode

    target = destination / NAME
    # L'extension doit rester lisible par Chrome : elle accompagne l'executable
    # au lieu d'etre enfermee dans le paquet.
    build_extensions.build('chrome', target / 'chrome-extension')
    build_extensions.build('firefox', target / 'firefox-extension')
    build_extensions.build('safari', target / 'safari-extension')
    for name in ('INSTALLATION_MULTINAVIGATEUR.md',):
        if (ROOT / name).exists():
            shutil.copyfile(ROOT / name, target / name)

    write_launchers(target)
    for name in ('README.md','LICENSE'):
        if (ROOT/name).is_file():shutil.copyfile(ROOT/name,target/name)
    if (ROOT/'docs').is_dir():shutil.copytree(ROOT/'docs',target/'docs')
    if sys.platform=='win32':
        for name,original in (('INSTALL.cmd','INSTALLER D ABORD.cmd'),('START.cmd','DEMARRER.cmd')):
            (target/name).write_text('@echo off\r\ncall "%~dp0'+original+'"\r\n',encoding='utf-8')
    (target / 'LISEZ-MOI.txt').write_text('Archivage X\n\nConsultez INSTALLATION_MULTINAVIGATEUR.md pour installer le service et choisir Chrome, Firefox ou Safari.\nLe dossier entier accompagne le programme. Aucune session ni archive personnelle n’est incluse.\n', encoding='utf-8')
    exe = target / (NAME + ('.exe' if sys.platform == 'win32' else ''))
    size = sum(f.stat().st_size for f in target.rglob('*') if f.is_file())
    print(f'\n  {exe}')
    print(f'  {size / 1e6:.0f} Mo au total')
    print('  Distribuez le dossier entier : l executable seul ne suffit pas.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
