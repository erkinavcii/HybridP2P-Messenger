import flet as ft
import asyncio
import threading

def main(page: ft.Page):
    fp = ft.FilePicker()
    
    async def btn_click(e):
        try:
            print("calling pick_files")
            res = await fp.pick_files()
            print("res:", res)
        except Exception as ex:
            print("pick_files exception:", repr(ex))
            
    page.add(ft.ElevatedButton("Pick", on_click=btn_click))
    
    # auto click the button after 1s
    def click_it():
        import time
        time.sleep(1)
        asyncio.run_coroutine_threadsafe(btn_click(None), page.loop)
        
    threading.Thread(target=click_it).start()

def stop_app():
    import time
    time.sleep(4)
    import os
    os._exit(0)

threading.Thread(target=stop_app).start()
ft.app(target=main)
