#include "ParameterPickerDialog.hpp"

#include <algorithm>
#include <cctype>
#include <utility>

#include <wx/button.h>
#include <wx/sizer.h>
#include <wx/stattext.h>
#include <wx/translation.h>

namespace Slic3r { namespace GUI {

namespace {

enum Column { ColSelected = 0, ColLabel, ColCategory, ColValue, ColKey };

std::string lowered(std::string s)
{
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return char(std::tolower(c)); });
    return s;
}

} // namespace

ParameterPickerDialog::ParameterPickerDialog(wxWindow* parent, const wxString& title, std::vector<ParameterPickerRow> rows)
    : wxDialog(parent, wxID_ANY, title, wxDefaultPosition, wxSize(900, 640), wxDEFAULT_DIALOG_STYLE | wxRESIZE_BORDER)
    , m_rows(std::move(rows))
{
    auto* sizer = new wxBoxSizer(wxVERTICAL);

    sizer->Add(new wxStaticText(this, wxID_ANY, _L("Tick the parameters to include and edit their values. Double-click a value to edit it.")),
               0, wxALL, 8);

    m_search = new wxSearchCtrl(this, wxID_ANY);
    m_search->ShowCancelButton(true);
    m_search->SetDescriptiveText(_L("Filter by label, category or key"));
    sizer->Add(m_search, 0, wxLEFT | wxRIGHT | wxEXPAND, 8);

    m_list = new wxDataViewListCtrl(this, wxID_ANY, wxDefaultPosition, wxDefaultSize, wxDV_ROW_LINES | wxDV_SINGLE);
    m_list->AppendToggleColumn(wxEmptyString, wxDATAVIEW_CELL_ACTIVATABLE, 32);
    m_list->AppendTextColumn(_L("Parameter"), wxDATAVIEW_CELL_INERT, 260);
    m_list->AppendTextColumn(_L("Category"), wxDATAVIEW_CELL_INERT, 140);
    m_list->AppendTextColumn(_L("Value"), wxDATAVIEW_CELL_EDITABLE, 200);
    m_list->AppendTextColumn(_L("Key"), wxDATAVIEW_CELL_INERT, 200);
    sizer->Add(m_list, 1, wxALL | wxEXPAND, 8);

    sizer->Add(CreateStdDialogButtonSizer(wxOK | wxCANCEL), 0, wxALL | wxALIGN_RIGHT, 8);
    SetSizer(sizer);

    m_search->Bind(wxEVT_TEXT, [this](wxCommandEvent&) { rebuild_view(); });
    m_list->Bind(wxEVT_DATAVIEW_ITEM_VALUE_CHANGED, &ParameterPickerDialog::on_item_changed, this);

    rebuild_view();
}

void ParameterPickerDialog::rebuild_view()
{
    const std::string filter = lowered(m_search->GetValue().ToStdString());

    m_rebuilding = true;
    m_list->DeleteAllItems();
    m_visible.clear();
    for (size_t i = 0; i < m_rows.size(); ++i) {
        const ParameterPickerRow& row = m_rows[i];
        if (!filter.empty() && lowered(row.label).find(filter) == std::string::npos &&
            lowered(row.category).find(filter) == std::string::npos && lowered(row.key).find(filter) == std::string::npos)
            continue;
        wxVector<wxVariant> cells;
        cells.push_back(wxVariant(row.selected));
        cells.push_back(wxVariant(wxString::FromUTF8(row.label)));
        cells.push_back(wxVariant(wxString::FromUTF8(row.category)));
        cells.push_back(wxVariant(wxString::FromUTF8(row.value)));
        cells.push_back(wxVariant(wxString::FromUTF8(row.key)));
        m_list->AppendItem(cells);
        m_visible.push_back(i);
    }
    m_rebuilding = false;
}

void ParameterPickerDialog::on_item_changed(wxDataViewEvent& evt)
{
    if (m_rebuilding)
        return;
    const int row = m_list->ItemToRow(evt.GetItem());
    if (row < 0 || size_t(row) >= m_visible.size())
        return;

    ParameterPickerRow& target = m_rows[m_visible[size_t(row)]];
    wxVariant           selected;
    wxVariant           value;
    m_list->GetValue(selected, unsigned(row), ColSelected);
    m_list->GetValue(value, unsigned(row), ColValue);
    target.selected = selected.GetBool();
    target.value    = value.GetString().ToStdString(wxConvUTF8);
    // Editing a value implies the user wants it included.
    if (evt.GetColumn() == int(ColValue) && !target.selected) {
        target.selected = true;
        m_list->SetValue(wxVariant(true), unsigned(row), ColSelected);
    }
}

std::vector<ParameterPickerRow> ParameterPickerDialog::selected_rows() const
{
    std::vector<ParameterPickerRow> out;
    for (const ParameterPickerRow& row : m_rows)
        if (row.selected)
            out.push_back(row);
    return out;
}

}} // namespace Slic3r::GUI
