# create-draws-ui

A custom Monday.com board view for creating construction draws.  React and Vite, built on the Monday SDK, running inside Monday.com itself.  The backend is [draw-service](https://github.com/SM197961/draw-service), a FastAPI service.

## What it does

Select loans on your servicing board, open this view, and create draws against them without leaving Monday.com:

- Reads your selected board items live through the Monday SDK context
- Enter one amount and apply it to every selected loan, or set amounts per loan
- One click creates all the draws through the draw-service API, with duplicate protection on the service side
- Shows the API result for each item, with a debug panel for the raw Monday context

## Why a custom app instead of an automation

Monday.com automations are good at moving statuses.  They are not good at "take these six selected loans, ask a human for six amounts, then create six linked records with the right draw numbers."  That takes an app.  This is the app.

## Running it
