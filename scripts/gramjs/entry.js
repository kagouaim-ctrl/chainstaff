import { TelegramClient, Api } from "telegram";
import { StringSession } from "telegram/sessions";
import { NewMessage } from "telegram/events";
import qrcode from "qrcode-generator";
window.GramJS = { TelegramClient, Api, StringSession, NewMessage, qrcode };
