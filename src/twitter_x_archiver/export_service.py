"""Local result-export controls for the authenticated dashboard."""
import base64
import json
import platform
import shutil
import subprocess
import threading
from pathlib import Path
from .archive import atomic_json, now
from . import categories
from .export_results import export_results,output_path,display_path,record_export


class ExportManager:
    def __init__(self,data):
        self.data=Path(data);self.lock=threading.Lock();self.chooser_lock=threading.Lock()
        self.settings_path=self.data/'export_settings.json'
        self.status_path=self.data/'export_status.json'
        self.settings=json.loads(self.settings_path.read_text()) if self.settings_path.exists() else {
            'destination':str(self.data.parent/'resultats'),'include_automatic':False}
        if not self.settings_path.exists():atomic_json(self.settings_path,self.settings)
        self.status=json.loads(self.status_path.read_text()) if self.status_path.exists() else {'status':'idle'}
        if self.status.get('status')=='running':
            self.status={'status':'interrupted','message':'Export interrompu ; relancez-le. Les dossiers .partial ne sont pas des exports validés.'}
        self.thread=None

    def _reload(self):
        # Un export lance en ligne de commande ecrit les memes fichiers ;
        # sans relecture le tableau de bord afficherait un resultat perime.
        for path,attr in ((self.status_path,'status'),(self.settings_path,'settings')):
            try:
                if path.exists():
                    setattr(self,attr,json.loads(path.read_text(encoding='utf-8')))
            except (OSError,ValueError):
                pass

    def view(self):
        with self.lock:
            if not (self.thread and self.thread.is_alive()):
                self._reload()
            export=dict(self.status)
            # Un dossier d'export peut avoir ete deplace ou supprime depuis :
            # le tableau de bord ne doit pas continuer a le presenter comme disponible.
            if export.get('path') and not Path(export['path']).exists():
                export['folder_missing']=True
            return {'destination':display_path(self.settings['destination']),
                    'include_automatic':self.settings.get('include_automatic',False),
                    'categories':self.settings.get('categories',[]),'export':export}

    def start(self,destination,include_automatic=False,only=None):
        target=output_path(destination)
        with self.lock:
            if self.thread and self.thread.is_alive():raise ValueError('Un export est déjà en cours.')
            only=[categories.clean(c) for c in only] if only else None
            self.settings={'destination':str(target),'include_automatic':include_automatic is True,
                           'categories':only or []}
            self.status=record_export(self.data,target,include_automatic,
                                      {'status':'running','started_at':now(),'destination':display_path(target)})
            self.thread=threading.Thread(target=self._run,args=(target,include_automatic is True,only),daemon=True)
            self.thread.start()
        return self.view()

    def _run(self,target,include_automatic,only=None):
        try:
            result=export_results(self.data,target,include_automatic,only)
            state={**result,'finished_at':now()}
        except Exception as exc:
            # Exception texts can include sensitive source values; only a safe classification leaves the worker.
            state={'status':'error','error_class':type(exc).__name__,
                   'message':'Export non validé. Vérifiez le dossier, l’espace disponible et les sources. Le dossier .partial est conservé pour diagnostic.'}
        with self.lock:
            self.status=record_export(self.data,target,include_automatic,state)

    def choose(self, storage=False):
        if not self.chooser_lock.acquire(blocking=False):raise ValueError('Une fenêtre de sélection est déjà ouverte.')
        try:
            system=platform.system()
            if system=='Darwin':
                executable=shutil.which('osascript')
                # Catch only user cancellation; permissions and scripting errors remain failures.
                script='''try
    return POSIX path of (choose folder with prompt "Choisir le dossier des résultats X (CSV et médias)")
on error number -128
    return ""
end try
'''
                command=[executable,'-e',script]
                cancel_code=None
            else:
                # WSL can use the existing Windows picker through powershell.exe.
                executable=shutil.which('powershell.exe') or (shutil.which('powershell') if system=='Windows' else None)
                cancel_code=None
                if executable:
                    command=self._windows_folder_command(executable)
                else:
                    executable=shutil.which('zenity') if system=='Linux' else None
                    command=[executable,'--file-selection','--directory',
                             '--title=Choisir le dossier des résultats X (CSV et médias)']
                    cancel_code=1
            if not executable:raise ValueError('Sélection graphique indisponible ; saisissez le chemin du dossier.')
            if storage:
                if '-EncodedCommand' in command:
                    script=base64.b64decode(command[-1]).decode('utf-16le')
                    command[-1]=base64.b64encode(script.replace('des résultats X (CSV et médias)', 'de stockage des archives X').encode('utf-16le')).decode('ascii')
                else:
                    command=[part.replace('des résultats X (CSV et médias)', 'de stockage des archives X') for part in command]
            rep=subprocess.run(command,capture_output=True,timeout=180)
            if cancel_code is not None and rep.returncode==cancel_code:
                return {'cancelled':True,'destination':''}
            if rep.returncode:raise ValueError('Sélection graphique indisponible ; saisissez le chemin du dossier.')
            # osascript/zenity append a newline; do not strip spaces from real folder names.
            selected=rep.stdout.decode('utf-8-sig').removesuffix('\n').removesuffix('\r')
            return {'cancelled':not bool(selected),'destination':selected}
        except subprocess.TimeoutExpired:
            raise ValueError('Sélection expirée ; saisissez le chemin ou ouvrez à nouveau la fenêtre.') from None
        except (OSError,UnicodeError):
            raise ValueError('Sélection graphique indisponible ; saisissez le chemin du dossier.') from None
        finally:self.chooser_lock.release()

    @staticmethod
    def _windows_folder_command(executable):
        # Fixed script: no user-controlled text is interpolated into PowerShell.
        script='''$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog=New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description='Choisir le dossier des résultats X (CSV et médias)'
$dialog.ShowNewFolderButton=$true
if($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){[Console]::Write($dialog.SelectedPath)}
$dialog.Dispose()
'''
        encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
        return [executable,'-NoProfile','-STA','-EncodedCommand',encoded]
