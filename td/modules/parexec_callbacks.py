"""
Parameter Execute DAT callbacks for the unicorner_controller COMP.

This file gets read at scaffold-time and stuffed into `parexec1.text` inside
the drop-in COMP. It runs in TD's Parameter Execute scope, with TD-injected
globals (par, parent, debug, etc.). It dispatches param changes/pulses on the
parent COMP into the corresponding callbacks module functions.

Param map:
  Scene        → onSceneChanged()      (value change → re-broadcast scene)
  Generate     → onGeneratePulse()     (pulse → start a generate from `Prompt`)
  Savekey      → onSaveKeyPulse()      (pulse → write Apikey to config file)
  Openbrowser  → onOpenBrowserPulse()  (pulse → open the iPad URL)
  Refreshurl   → onRefreshUrlPulse()   (pulse → recompute and display the URL)
"""


def onValueChange(par, prev):
    if par.name == 'Scene':
        try:
            parent().op('callbacks').module.onSceneChanged()
        except Exception as e:
            debug(f'unicorner_controller: scene change failed: {e}')
    return


def onPulse(par):
    handlers = {
        'Generate':    'onGeneratePulse',
        'Savekey':     'onSaveKeyPulse',
        'Openbrowser': 'onOpenBrowserPulse',
        'Refreshurl':  'onRefreshUrlPulse',
    }
    handler = handlers.get(par.name)
    if handler is None:
        return
    try:
        getattr(parent().op('callbacks').module, handler)()
    except Exception as e:
        debug(f'unicorner_controller: {par.name} pulse failed: {e}')
    return


def onExpressionChange(par, val, prev): return
def onExportChange(par, val, prev): return
def onEnableChange(par, val, prev): return
def onModeChange(par, val, prev): return
def onValuesChanged(changes): return
