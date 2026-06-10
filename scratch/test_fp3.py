import flet as ft
import asyncio
import threading

def main(page: ft.Page):
    fp = ft.FilePicker()
    # Let's try adding it to overlay to see if it fails
    try:
        page.overlay.append(fp)
        page.update()
        print("overlay append success")
    except Exception as e:
        print("overlay append exception:", e)

    async def btn_click(e):
        try:
            print("calling pick_files")
            res = await fp.pick_files()
            print("res:", res)
        except Exception as ex:
            print("pick_files exception:", ex)
            
    page.add(ft.ElevatedButton("Pick", on_click=btn_click))

def stop_app():
    import time
    time.sleep(2)
    import os
    os._exit(0)

threading.Thread(target=stop_app).start()
ft.app(target=main)
