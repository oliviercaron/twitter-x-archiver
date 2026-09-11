// The dashboard can choose a language independently from the browser UI.
// Keep the small visible extension surface usable when the browser itself is
// French (or when a browser does not expose i18n substitutions consistently).
(() => {
  const EN = {
    extensionName: 'X Archive',
    extensionDescription: 'Pick an X post to archive its metadata and media into your local folder.',
    actionTitle: 'Archive an X post', contextArchive: 'Archive this post',
    stateQueued: 'Waiting', stateFetching: 'Fetching…', stateDone: 'Archived',
    statePartial: 'Partial, try again', stateRetry: 'Try again', stateUnavailable: 'Unavailable on X',
    buttonArchive: 'Archive', tipArchive: 'Archive this post and its media',
    tipAlreadyArchived: 'Already archived: open the archive page',
    tipState: state => `${state} (local archive)`, tipStateError: (state, reason) => `${state}: ${reason}`,
    queuing: 'Adding…', tipDelete: 'Delete this archive: text, counts, media and post data',
    confirmDelete: 'Delete?', tipConfirmDelete: 'Click again to delete. Nothing can be recovered.',
    deleting: 'Deleting…', deleteShared: 'Shared media', deleteFailed: 'Failed',
    tipDeleteShared: 'Another post uses a file from this archive too. Deletion refused.',
    leftBehind: 'Some files remain',
    errorUnreachable: 'The archiving service does not answer. Open the application folder and run DEMARRER.cmd.',
    errorStillDown: 'The service still does not answer. Open http://127.0.0.1:18765 to see why.',
    errorStartRefused: 'The archiving service could not start. Close it if it is already open, then try again.',
    errorNotInstalled: 'Automatic start is not set up. Run INSTALLER D ABORD.cmd once, in the application folder.',
    errorNotPaired: 'Open the archive page (http://127.0.0.1:18765) and connect the extension.',
    errorPairAgain: 'Open the archive page and connect the extension again.', errorRefused: 'The archiving service refused the request.',
    errorOrigin: 'Origin not allowed.', errorUnknown: 'Unknown command.', errorBadId: 'Invalid identifier.',
    errorBadUrl: 'Open an X post, or paste its address.', errorNoExtension: 'Extension unreachable.',
    errorPairRefused: 'Connection not allowed.', errorNoSession: 'Sign in to X in this browser, then try again.',
    errorNoSessionApi: 'X session unavailable', errorPairFailed: 'Connection failed',
    pairStatusReading: 'Fetching your X connection…',
    popupTitle: '↓ Archive this post', popupIntro: 'Text, media and counts in your local folder.',
    popupUrlLabel: 'Post address', popupNoteLabel: 'Note, optional', popupNoteHint: 'Topic, context…',
    popupReplies: 'Archive the replies as well', popupRepliesHint: 'This choice also applies to the button under posts and to right-click.',
    popupRefresh: 'Read the counts again for a post already archived', popupSubmit: 'Archive',
    popupLink: 'Open the full archive ↗', popupQueued: 'Added to the queue. The download happens locally.',
    popupAlready: 'Already archived ✓', popupFailed: 'Request failed', popupCategoryLabel: 'Category', popupCategoryNew: 'New category…',
    catMoveInstead: 'Move instead to', catMoveTip: category => `Move this post to “${category}”`,
    catMoved: category => `Moved to ${category}`, catMoveFailed: 'Could not move it', reloadTab: 'Reload this tab',
    catNewShort: 'Other…', catNewTip: 'Find or create a category', catPromptShort: 'Search categories',
    catCurrent: category => `Category: ${category}`, catCreate: category => `Create “${category}”`,
    catUnknown: 'Category unavailable', catSuggestions: 'Suggested categories',
    errorCompanionRequired: 'Open the Archivage X companion app on this Mac, then try again.'
  };

  globalThis.archiveMessage = (extension, language, key, args = []) => {
    if (language === 'en' && Object.prototype.hasOwnProperty.call(EN, key)) {
      const value = EN[key];
      return typeof value === 'function' ? value(...args) : value;
    }
    try {
      const value = extension.i18n.getMessage(key, args.length ? args : undefined);
      if (value) return value;
    } catch {}
    const fallback = EN[key];
    return typeof fallback === 'function' ? fallback(...args) : (fallback || key);
  };
})();
