import { useState, type FormEvent } from "react"
import { REGEXP_ONLY_DIGITS } from "input-otp"
import { KeyRoundIcon, LoaderCircleIcon, LockKeyholeIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp"
import type { AccessController } from "@/hooks/use-access"

export function PairingScreen({ access }: { access: AccessController }) {
  const [code, setCode] = useState("")

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (code.length !== 4) return
    await access.pair(code)
  }

  return (
    <div className="grid min-h-svh place-items-center px-5 py-10">
      <div className="w-full max-w-sm rounded-3xl border border-white/5 bg-card/85 p-6 shadow-2xl shadow-black/30 backdrop-blur-xl">
        <div className="mx-auto grid size-14 place-items-center rounded-2xl border border-violet-300/15 bg-violet-400/10 text-violet-200">
          <LockKeyholeIcon className="size-6" aria-hidden="true" />
        </div>
        <div className="mt-5 text-center">
          <h1 className="text-xl font-semibold">Pair with LumiStripe</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Enter the four-digit code configured on the controller.
          </p>
        </div>

        <form className="mt-6 space-y-4" onSubmit={(event) => void submit(event)}>
          <div>
            <label htmlFor="pairing-code" className="mb-2 block text-sm font-medium">Pairing code</label>
            <InputOTP
              id="pairing-code"
              maxLength={4}
              pattern={REGEXP_ONLY_DIGITS}
              autoComplete="one-time-code"
              value={code}
              autoFocus
              disabled={access.pairing}
              aria-invalid={Boolean(access.error)}
              aria-describedby={access.error ? "pairing-error" : undefined}
              onChange={setCode}
              containerClassName="justify-center"
            >
              <InputOTPGroup>
                {[0, 1, 2, 3].map((index) => (
                  <InputOTPSlot
                    key={index}
                    index={index}
                    aria-invalid={Boolean(access.error)}
                    className="size-13 text-xl font-semibold tabular-nums"
                  />
                ))}
              </InputOTPGroup>
            </InputOTP>
            {access.error && (
              <p id="pairing-error" role="alert" className="mt-2 text-sm text-red-300">{access.error}</p>
            )}
          </div>
          <Button type="submit" size="lg" disabled={code.length !== 4 || access.pairing} className="h-12 w-full rounded-xl">
            {access.pairing ? <LoaderCircleIcon className="animate-spin" aria-hidden="true" /> : <KeyRoundIcon aria-hidden="true" />}
            {access.pairing ? "Pairing…" : "Pair device"}
          </Button>
        </form>

        <p className="mt-5 text-center text-xs text-muted-foreground">
          Failed attempts are temporarily rate limited.
        </p>
      </div>
    </div>
  )
}
